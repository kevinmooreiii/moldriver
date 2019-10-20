""" reaction list test
"""
import os
import numpy
import copy
from qcelemental import constants as qcc
import thermo
import automol
import autofile
import moldr
import mess_io
import mess_io.writer
import ratefit
import scripts

EH2KCAL = qcc.conversion_factor('hartree', 'kcal/mol')
ANG2BOHR = qcc.conversion_factor('angstrom', 'bohr')
WAVEN2KCAL = qcc.conversion_factor('wavenumber', 'kcal/mol')
EH2KCAL = qcc.conversion_factor('hartree', 'kcal/mol')
RATE_SCRIPT_STR = ("#!/usr/bin/env bash\n"
                   "mess mess.inp build.out >> stdout.log &> stderr.log")
PROJROT_SCRIPT_STR = ("#!/usr/bin/env bash\n"
                      "RPHt.exe >& /dev/null")

def pf_headers(
        rct_ichs, temps, press, exp_factor, exp_power, exp_cutoff, eps1, eps2,
        sig1, sig2, mass1):
    """ makes the standard header and energy transfer sections for MESS input file
    """
    # header section
    header_str = mess_io.writer.global_reaction(temps, press)
    print(header_str)
    tot_mass = 0.
    for rct_ich in rct_ichs:
        geo = automol.convert.inchi.geometry(rct_ich)
        masses = automol.geom.masses(geo)
        for mass in masses:
            tot_mass += mass

    # energy transfer section
    energy_trans_str = mess_io.writer.energy_transfer(
        exp_factor, exp_power, exp_cutoff, eps1, eps2, sig1, sig2, mass1, tot_mass)

    return header_str, energy_trans_str


def make_all_species_data(rxn_lst, spc_dct, save_prefix, model_info, pf_info, ts_found, projrot_script_str):
    """ generate the MESS species blocks for all the species
    """
    species = {}
    spc_save_fs = autofile.fs.species(save_prefix)
    for idx, rxn in enumerate(rxn_lst):
        tsname = 'ts_{:g}'.format(idx)
        if tsname in ts_found:
            ts = spc_dct[tsname]
            specieslist = rxn['reacs'] + rxn['prods']
            for name in specieslist:
                if not name in species:
                    species[name], _ = make_species_data(
                        name, spc_dct[name], spc_save_fs, model_info, pf_info, projrot_script_str)
            if not 'radical radical addition' in spc_dct[tsname]['class']:
                species[tsname], spc_dct[tsname]['imag_freq'] = make_species_data(
                    tsname, ts, save_prefix, model_info, pf_info, projrot_script_str)
    return species


def make_species_data(spc, spc_dct_i, spc_save_fs, spc_model, pf_levels, projrot_script_str):
    """ makes the main part of the MESS species block for a given species
    """
    spc_info = (spc_dct_i['ich'], spc_dct_i['chg'], spc_dct_i['mul'])
    if 'ts_' in spc:
        save_path = spc_dct_i['rxn_fs'][3]
    else:
        spc_save_fs.leaf.create(spc_info)
        save_path = spc_save_fs.leaf.path(spc_info)
    species_data = moldr.pf.species_block(
        spc=spc,
        spc_dct_i=spc_dct_i,
        spc_info=spc_info,
        spc_model=spc_model,
        pf_levels=pf_levels,
        projrot_script_str=projrot_script_str,
        elec_levels=[[0., 1]], sym_factor=1.,
        save_prefix=save_path,
        )
    return species_data


def make_fake_species_data(spc_dct_i, spc_dct_j, spc_save_fs, pf_levels, projrot_script_str):
    """ make a fake MESS species block to represent the van der Waals well
    arising from the combination of two fragment species
    """
    spc_info_i = (spc_dct_i['ich'], spc_dct_i['chg'], spc_dct_i['mul'])
    spc_info_j = (spc_dct_j['ich'], spc_dct_j['chg'], spc_dct_j['mul'])
    spc_save_fs.leaf.create(spc_info_i)
    spc_save_fs.leaf.create(spc_info_j)
    save_path_i = spc_save_fs.leaf.path(spc_info_i)
    save_path_j = spc_save_fs.leaf.path(spc_info_j)
    if pf_levels[3]:
        spc_model = ['RIGID', 'HARM', 'SAMPLING']
    else:
        spc_model = ['RIGID', 'HARM', '']
    species_data = moldr.pf.fake_species_block(
        spc_dct_i=spc_dct_i,
        spc_dct_j=spc_dct_j,
        spc_info_i=spc_info_i,
        spc_info_j=spc_info_j,
        spc_model=spc_model,
        pf_levels=pf_levels,
        projrot_script_str=projrot_script_str,
        elec_levels=[[0., 1]], sym_factor=1.,
        save_prefix_i=save_path_i,
        save_prefix_j=save_path_j
        )
    return species_data


def make_channel_pfs(
        tsname, rxn, species_data, spc_dct, idx_dct, strs, first_ground_ene,
        spc_save_fs, pf_levels, multi_info, projrot_script_str):
    """ make the partition function strings for each of the channels
    includes strings for each of the unimolecular wells, bimolecular fragments, and
    transition states connecting them.
    It also includes a special treatment for abstraction to include phase space blocks
    coupling bimolecular fragments to fake van der Waals wells
    """
    bim_str, well_str, ts_str = strs
#Find the number of uni and bimolecular wells already in the dictionary
    pidx = 1
    widx = 1
    fidx = 1
    for val in idx_dct.values():
        if 'P' in val:
            pidx += 1
        elif 'W' in val:
            widx += 1
        elif 'F' in val:
            fidx += 1
#Set up a new well for the reactants if that combo isn't already in the dct
    reac_label = ''
    reac_ene = 0.
    bimol = False
    if len(rxn['reacs']) > 1:
        bimol = True
    well_data = []
    spc_label = []
    for reac in rxn['reacs']:
        spc_label.append(automol.inchi.smiles(spc_dct[reac]['ich']))
        well_data.append(species_data[reac])
        reac_ene += scripts.thermo.spc_energy(spc_dct[reac]['ene'], spc_dct[reac]['zpe']) * EH2KCAL
    well_dct_key1 = '+'.join(rxn['reacs'])
    well_dct_key2 = '+'.join(rxn['reacs'][::-1])
    if not well_dct_key1 in idx_dct:
        if well_dct_key2 in idx_dct:
            well_dct_key1 = well_dct_key2
        else:
            if bimol:
                reac_label = 'P' + str(pidx)
                pidx += 1
                if not first_ground_ene:
                    first_ground_ene = reac_ene
                ground_energy = reac_ene - first_ground_ene
                bim_str += ' \t ! {} + {} \n'.format(rxn['reacs'][0], rxn['reacs'][1])
                bim_str += mess_io.writer.bimolecular(
                    reac_label, spc_label[0], well_data[0], spc_label[1],
                    well_data[1], ground_energy)
                idx_dct[well_dct_key1] = reac_label
            else:
                if not first_ground_ene:
                    first_ground_ene = reac_ene
                reac_label = 'W' + str(widx)
                widx += 1
                zero_energy = reac_ene - first_ground_ene
                well_str += ' \t ! {} \n'.format(rxn['reacs'][0])
                well_str += mess_io.writer.well(reac_label, well_data[0], zero_energy)
                idx_dct[well_dct_key1] = reac_label
    if not reac_label:
        reac_label = idx_dct[well_dct_key1]
#Set up a new well for the products if that combo isn't already in the dct
    prod_label = ''
    prod_ene = 0.
    bimol = False
    if len(rxn['prods']) > 1:
        bimol = True
    well_data = []
    spc_label = []
    for prod in rxn['prods']:
        spc_label.append(automol.inchi.smiles(spc_dct[prod]['ich']))
        well_data.append(species_data[prod])
        prod_ene += scripts.thermo.spc_energy(spc_dct[prod]['ene'], spc_dct[prod]['zpe']) * EH2KCAL
    zero_energy = prod_ene - reac_ene
    well_dct_key1 = '+'.join(rxn['prods'])
    well_dct_key2 = '+'.join(rxn['prods'][::-1])
    if not well_dct_key1 in idx_dct:
        if well_dct_key2 in idx_dct:
            well_dct_key1 = well_dct_key2
        else:
            if bimol:
                prod_label = 'P' + str(pidx)
                ground_energy = prod_ene - first_ground_ene
                bim_str += ' \t ! {} + {} \n'.format(
                    rxn['prods'][0], rxn['prods'][1])
                bim_str += mess_io.writer.bimolecular(
                    prod_label, spc_label[0], well_data[0], spc_label[1], well_data[1],
                    ground_energy)
                idx_dct[well_dct_key1] = prod_label
            else:
                prod_label = 'W' + str(widx)
                zero_energy = prod_ene - first_ground_ene
                well_str += ' \t ! {} \n'.format(rxn['prods'][0])
                well_str += mess_io.writer.well(prod_label, well_data[0], zero_energy)
                idx_dct[well_dct_key1] = prod_label
    if not prod_label:
        prod_label = idx_dct[well_dct_key1]

    #Set up a new well connected to ts
    if 'radical radical' in spc_dct[tsname]['class']:
        # for radical radical call vtst or vrctst
        ts_label = 'B' + str(int(tsname.replace('ts_', ''))+1)
        if 'P' in reac_label:
            spc_ene = reac_ene - first_ground_ene
            spc_zpe = spc_dct[rxn['reacs'][0]]['zpe'] + spc_dct[rxn['reacs'][1]]['zpe']
        else:
            spc_ene = prod_ene - first_ground_ene
            spc_zpe = spc_dct[rxn['prods'][0]]['zpe'] + spc_dct[rxn['prods'][1]]['zpe']
        ts_str += '\n' + moldr.pf.vtst_with_no_saddle_block(
            spc_dct[tsname], ts_label, reac_label, prod_label, spc_ene, spc_zpe, projrot_script_str,
            multi_info, elec_levels=[[0., 1]], sym_factor=1.
            )

    else:
        # for tight TS's make ts_str
        # for abstraction make fake wells and PST TSs
        ts_ene = scripts.thermo.spc_energy(spc_dct[tsname]['ene'], spc_dct[tsname]['zpe']) * EH2KCAL
        zero_energy = ts_ene - first_ground_ene
        ts_label = 'B' + str(int(tsname.replace('ts_', ''))+1)
        imag_freq = 0
        if 'imag_freq' in spc_dct[tsname]:
            imag_freq = abs(spc_dct[tsname]['imag_freq'])
        if not imag_freq:
            print('No imaginary freq for ts: {}'.format(tsname))


        fake_wellr_label = ''
        fake_wellp_label = ''
        if 'abstraction' in spc_dct[tsname]['class']:
        #Make fake wells and PST TSs as needed
            well_dct_key1 = 'F' + '+'.join(rxn['reacs'])
            well_dct_key2 = 'F' + '+'.join(rxn['reacs'][::-1])
            if not well_dct_key1 in idx_dct:
                if well_dct_key2 in idx_dct:
                    well_dct_key1 = well_dct_key2
                else:
                    fake_wellr_label = 'F' + str(fidx)
                    fidx += 1
                    vdwr_ene = reac_ene - 1.0
                    zero_energy = vdwr_ene - first_ground_ene
                    well_str += ' \t ! Fake Well for {}\n'.format('+'.join(rxn['reacs']))
                    fake_wellr = make_fake_species_data(
                        spc_dct[rxn['reacs'][0]], spc_dct[rxn['reacs'][1]],
                        spc_save_fs, pf_levels, projrot_script_str)
                    well_str += mess_io.writer.well(fake_wellr_label, fake_wellr, zero_energy)
                    idx_dct[well_dct_key1] = fake_wellr_label

                    pst_r_label = 'FRB' + str(int(tsname.replace('ts_', ''))+1)
                    idx_dct[well_dct_key1.replace('F', 'FRB')] = pst_r_label
                    spc_dct_i = spc_dct[rxn['reacs'][0]]
                    spc_dct_j = spc_dct[rxn['reacs'][1]]
                    if pf_levels[3]:
                        spc_model = ['RIGID', 'HARM', 'SAMPLING']
                    else:
                        spc_model = ['RIGID', 'HARM', '']
                    pst_r_ts_str = moldr.pf.pst_block(
                        spc_dct_i, spc_dct_j, spc_model=spc_model,
                        pf_levels=pf_levels, projrot_script_str=projrot_script_str,
                        spc_save_fs=spc_save_fs)
            print('fake_wellr_label test:', fake_wellr_label)
            if not fake_wellr_label:
                print('well_dct_key1 test:', well_dct_key1)
                fake_wellr_label = idx_dct[well_dct_key1]
                pst_r_label = idx_dct[well_dct_key1.replace('F', 'FRB')]
            zero_energy = reac_ene - first_ground_ene
            tunnel_str = ''
            ts_str += '\n' + mess_io.writer.ts_sadpt(
                pst_r_label, reac_label, fake_wellr_label, pst_r_ts_str,
                zero_energy, tunnel_str)

            well_dct_key1 = 'F' + '+'.join(rxn['prods'])
            well_dct_key2 = 'F' + '+'.join(rxn['prods'][::-1])
            if not well_dct_key1 in idx_dct:
                if well_dct_key2 in idx_dct:
                    well_dct_key1 = well_dct_key2
                else:
                    fake_wellp_label = 'F' + str(fidx)
                    fidx += 1
                    vdwp_ene = prod_ene - 1.0
                    zero_energy = vdwp_ene - first_ground_ene
                    well_str += ' \t ! Fake Well for {}\n'.format('+'.join(rxn['prods']))
                    fake_wellp = make_fake_species_data(
                        spc_dct[rxn['prods'][0]], spc_dct[rxn['prods'][1]],
                        spc_save_fs, pf_levels, projrot_script_str)
                    well_str += mess_io.writer.well(fake_wellp_label, fake_wellp, zero_energy)
                    idx_dct[well_dct_key1] = fake_wellp_label

                    pst_p_label = 'FPB' + str(int(tsname.replace('ts_', ''))+1)
                    idx_dct[well_dct_key1.replace('F', 'FPB')] = pst_p_label
                    spc_dct_i = spc_dct[rxn['prods'][0]]
                    spc_dct_j = spc_dct[rxn['prods'][1]]
                    if pf_levels[3]:
                        spc_model = ['RIGID', 'HARM', 'SAMPLING']
                    else:
                        spc_model = ['RIGID', 'HARM', '']
                    pst_p_ts_str = moldr.pf.pst_block(
                        spc_dct_i, spc_dct_j, spc_model=spc_model,
                        pf_levels=pf_levels, projrot_script_str=projrot_script_str,
                        spc_save_fs=spc_save_fs)
            if not fake_wellp_label:
                fake_wellp_label = idx_dct[well_dct_key1]
                pst_p_label = idx_dct[well_dct_key1.replace('F', 'FPB')]
            zero_energy = prod_ene - first_ground_ene
            tunnel_str = ''
            ts_str += '\n' + mess_io.writer.ts_sadpt(
                pst_p_label, prod_label, fake_wellp_label, pst_p_ts_str,
                zero_energy, tunnel_str)

            zero_energy = ts_ene - first_ground_ene
            ts_reac_barr = ts_ene - vdwr_ene
            ts_prod_barr = ts_ene - vdwp_ene
            if ts_reac_barr < 0.:
                ts_reac_barr = 0.1
            if ts_prod_barr < 0.:
                ts_prod_barr = 0.1
            tunnel_str = mess_io.writer.tunnel_eckart(
                imag_freq, ts_reac_barr, ts_prod_barr)
            ts_str += '\n' + mess_io.writer.ts_sadpt(
                ts_label, fake_wellr_label, fake_wellp_label, species_data[tsname], zero_energy, tunnel_str)
        else:
            ts_reac_barr = ts_ene - reac_ene
            ts_prod_barr = ts_ene - prod_ene
            if ts_reac_barr < 0.:
                ts_reac_barr = 0.1
            if ts_prod_barr < 0.:
                ts_prod_barr = 0.1
            tunnel_str = mess_io.writer.tunnel_eckart(
                imag_freq, ts_reac_barr, ts_prod_barr)
            ts_str += '\n' + mess_io.writer.ts_sadpt(
                ts_label, reac_label, prod_label, species_data[tsname], zero_energy, tunnel_str)

    return [well_str, bim_str, ts_str], first_ground_ene


def run_rates(
        header_str, energy_trans_str, well_str, bim_str, ts_str, tsdct,
        thy_info, rxn_save_path):
    """ Generate k(T,P) by first compiling all the MESS strings and then running MESS
    """
    ts_info = (tsdct['ich'], tsdct['chg'], tsdct['mul'])
    orb_restr = moldr.util.orbital_restriction(ts_info, thy_info)
    ref_level = thy_info[1:3]
    ref_level.append(orb_restr)
    print('ref level test:', ref_level)
    thy_save_fs = autofile.fs.theory(rxn_save_path)
    thy_save_fs.leaf.create(ref_level)
    thy_save_path = thy_save_fs.leaf.path(ref_level)

    mess_inp_str = '\n'.join([header_str, energy_trans_str, well_str, bim_str, ts_str])
    print('mess input file')
    print(mess_inp_str)

    bld_locs = ['MESS', 0]
    bld_save_fs = autofile.fs.build(thy_save_path)
    bld_save_fs.leaf.create(bld_locs)
    mess_path = bld_save_fs.leaf.path(bld_locs)
    print('Build Path for MESS rate files:')
    print(mess_path)
    with open(os.path.join(mess_path, 'mess.inp'), 'w') as mess_file:
        mess_file.write(mess_inp_str)
    moldr.util.run_script(RATE_SCRIPT_STR, mess_path)
    return mess_path


def read_rates(rct_lab, prd_lab, mess_path, assess_pdep_temps,
               pdep_tolerance=20.0, no_pdep_pval=1.0,
               pdep_low=None, pdep_high=None):
    """ Read the rate constants from the MESS output and
        (1) filter out the invalid rates that are negative or undefined
        and obtain the pressure dependent values
    """

    # Dictionaries to store info; indexed by pressure (given in fit_ps)
    calc_k_dct = {}
    valid_calc_tk_dct = {}
    ktp_dct = {}

    # Read the MESS output file into a string
    with open(mess_path+'/rate.out', 'r') as mess_file:
        output_string = mess_file.read()
    # with open(mess_path+'/mess.inp', 'r') as mess_file:
    #     input_string = mess_file.read()

    # Read the temperatures and pressures out of the MESS output
    mess_temps, _ = mess_io.reader.rates.get_temperatures(
        output_string)
    mess_pressures, punit = mess_io.reader.rates.get_pressures(
        output_string)
    # mess_pressures, punit = mess_io.reader.rates.get_pressures_input(
    #     input_string)

    # Loop over the pressures obtained from the MESS output
    for pressure in mess_pressures:

        # Read the rate constants
        if pressure == 'high':
            rate_ks = mess_io.reader.highp_ks(
                output_string, rct_lab, prd_lab)
        else:
            rate_ks = mess_io.reader.pdep_ks(
                output_string, rct_lab, prd_lab, pressure, punit)

        # Store in a dictionary
        calc_k_dct[pressure] = rate_ks

    # Remove k(T) vals at each P where where k is negative or undefined
    # If ANY valid k(T,P) vals at given pressure, store in dct
    for pressure, calc_ks in calc_k_dct.items():
        filtered_temps, filtered_ks = ratefit.fit.util.get_valid_tk(
            mess_temps, calc_ks)
        if filtered_ks.size > 0:
            valid_calc_tk_dct[pressure] = [filtered_temps, filtered_ks]

    # Filter the ktp dictionary by assessing the presure dependence
    rxn_is_pdependent = ratefit.err.assess_pressure_dependence(
        valid_calc_tk_dct, assess_pdep_temps,
        tolerance=pdep_tolerance, plow=pdep_low, phigh=pdep_high)

    if rxn_is_pdependent:
        # Set dct to fit as copy of dct to do PLOG fits at all pressures
        ktp_dct = copy.deepcopy(valid_calc_tk_dct)
    else:
        # Set dct to have single set of k(T, P) vals: P is desired pressure
        ktp_dct['high'] = valid_calc_tk_dct[no_pdep_pval]
        # premap = no_pdep_pval

    return ktp_dct


def mod_arr_fit(ktp_dct, mess_path, fit_type='single', fit_method='dsarrfit',
                t_ref=1.0, a_conv_factor=1.0):
    """
    Routine for a single reaction:
        (1) Grab high-pressure and pressure-dependent rate constants
            from a MESS output file
        (2) Fit rate constants to an Arrhenius expression
    """

    assert fit_type in ('single', 'double')
    assert fit_method == 'dsarrfit'

    # Dictionaries to store info; indexed by pressure (given in fit_ps)
    fit_param_dct = {}
    fit_k_dct = {}
    fit_err_dct = {}

    # Calculate the fitting parameters from the filtered T,k lists
    for pressure, tk_lsts in ktp_dct.items():

        # Set the temperatures and rate constants
        temps = numpy.array(tk_lsts[0])
        rate_constants = numpy.array(tk_lsts[1])

        # Fit rate constants using desired Arrhenius fit
        if fit_type == 'single':
            fit_params = ratefit.fit.arrhenius.single(
                temps, rate_constants, t_ref, fit_method,
                dsarrfit_path=mess_path, a_conv_factor=a_conv_factor)
        elif fit_type == 'double':
            fit_params = ratefit.fit.arrhenius.double(
                temps, rate_constants, t_ref, fit_method,
                dsarrfit_path=mess_path, a_conv_factor=a_conv_factor)

        # Store the fitting parameters in a dictionary
        fit_param_dct[pressure] = fit_params

    # Calculate fitted rate constants using the fitted parameters
    for pressure, params in fit_param_dct.items():

        # Set the temperatures
        # temps = valid_calc_tk_dct[pressure][0]
        temps = numpy.array(ktp_dct[pressure][0])

        # Calculate fitted rate constants, based on fit type
        if fit_type == 'single':
            fit_ks = ratefit.fxns.single_arrhenius(
                params[0], params[1], params[2],
                t_ref, temps)
        elif fit_type == 'double':
            fit_ks = ratefit.fxns.double_arrhenius(
                params[0], params[1], params[2],
                params[3], params[4], params[5],
                t_ref, temps)

        # Store the fitting parameters in a dictionary
        fit_k_dct[pressure] = fit_ks / a_conv_factor

    # Calculute the error between the calc and fit ks
    for pressure, fit_ks in fit_k_dct.items():

        # if pressure == 'high' and not rxn_is_pdependent:
        #     pressure = premap
        # calc_ks = valid_calc_tk_dct[pressure][1]
        calc_ks = ktp_dct[pressure][1]
        mean_avg_err, max_avg_err = ratefit.err.calc_sse_and_mae(
            calc_ks, fit_ks)

        # Store in a dictionary
        fit_err_dct[pressure] = [mean_avg_err, max_avg_err]

    # If no high-pressure is found in dct, add fake fitting parameters
    if 'high' not in fit_param_dct:
        if fit_type == 'single':
            fit_param_dct['high'] = [1.00, 0.00, 0.00]
        elif fit_type == 'double':
            fit_param_dct['high'] = [1.00, 0.00, 0.00, 1.00, 0.00, 0.00]

    return fit_param_dct, fit_err_dct


