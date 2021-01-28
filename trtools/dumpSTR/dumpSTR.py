#!/usr/bin/env python3
"""
Tool for filtering and QC of TR genotypes
"""

# Load external libraries
import argparse
import collections
import itertools
import os
import subprocess as sp
import sys
import time
from typing import Dict, List, Set

import cyvcf2
import numpy as np

from . import filters as filters
import trtools.utils.common as common
import trtools.utils.tr_harmonizer as trh
import trtools.utils.utils as utils
from trtools import __version__


def MakeWriter(outfile, invcf, command):
    r"""Create a VCF writer with a dumpSTR header

    Adds a header line with the dumpSTR command used

    Parameters
    ----------
    outfile : str
       Name of the output file
    invcf : vcf.Reader object
       Input VCF. Used to grab header info
    command : str
       String command used to run dumpSTR

    Returns
    -------
    writer : vcf.Writer object
       VCF writer initialized with header of input VCF
       Set to None if we had a problem writing the file
    """
    invcf.add_to_header("##command-DumpSTR=" + command)
    writer = cyvcf2.Writer(outfile, invcf)
    return writer

def CheckLocusFilters(args, vcftype):
    r"""Perform checks on user inputs for locus-level filters

    Parameters
    ----------
    args : argparse namespace
        Contains user arguments
    vcftype : enum.
        Specifies which tool this VCF came from.
        Must be included in trh.VCFTYPES

    Returns
    -------
    checks : bool
        Set to True if all filters look ok.
        Set to False if filters are invalid
    """
    if args.min_locus_hwep is not None:
        if args.min_locus_hwep < 0 or args.min_locus_hwep > 1:
            common.WARNING("Invalid --min-locus-hwep. Must be between 0 and 1")
            return False
    if args.min_locus_het is not None:
        if args.min_locus_het < 0 or args.min_locus_het > 1:
            common.WARNING("Invalid --min-locus-het. Must be between 0 and 1")
            return False
    if args.max_locus_het is not None:
        if args.max_locus_het < 0 or args.max_locus_het > 1:
            common.WARNING("Invalid --max-locus-het. Must be between 0 and 1")
            return False
    if args.min_locus_het is not None and args.max_locus_het is not None:
        if args.max_locus_het < args.min_locus_het:
            common.WARNING("Cannot have --max-locus-het less than --min-locus-het")
            return False
    if args.use_length and vcftype not in [trh.VcfTypes["hipstr"]]:
        common.WARNING("--use-length is only meaningful for HipSTR, which reports sequence level differences.")
    if args.filter_hrun and vcftype not in [trh.VcfTypes["hipstr"]]:
        common.WARNING("--filter-hrun only relevant to HipSTR files. This filter will have no effect.")
    if args.filter_regions is not None:
        if args.filter_regions_names is not None:
            filter_region_files = args.filter_regions.split(",")
            filter_region_names = args.filter_regions_names.split(",")
            if len(filter_region_names) != len(filter_region_files):
                common.WARNING("Length of --filter-regions-names must match --filter-regions.")
                return False
    return True

def CheckHipSTRFilters(format_fields, args):
    r"""Check HipSTR call-level filters

    Parameters
    ----------
    format_fields :
        The format fields used in this VCF
    args : argparse namespace
        Contains user arguments

    Returns
    -------
    checks : bool
        Set to True if all filters look ok.
        Set to False if filters are invalid
    """
    if args.hipstr_max_call_flank_indel is not None:
        if args.hipstr_max_call_flank_indel < 0 or args.hipstr_max_call_flank_indel > 1:
            common.WARNING("--hipstr-max-call-flank-indel must be between 0 and 1")
            return False
        assert "DP" in format_fields and "DFLANKINDEL" in format_fields # should always be true
    if args.hipstr_max_call_stutter is not None:
        if args.hipstr_max_call_stutter < 0 or args.hipstr_max_call_stutter > 1:
            common.WARNING("--hipstr-max-call-stutter must be between 0 and 1")
            return False
        assert "DP" in format_fields and "DSTUTTER" in format_fields # should always be true
    if args.hipstr_min_supp_reads is not None:
        if args.hipstr_min_supp_reads < 0:
            common.WARNING("--hipstr-min-supp-reads must be >= 0")
            return False
        assert "ALLREADS" in format_fields and "GB" in format_fields
    if args.hipstr_min_call_DP is not None:
        if args.hipstr_min_call_DP < 0:
            common.WARNING("--hipstr-min-call-DP must be >= 0")
            return False
        assert "DP" in format_fields
    if args.hipstr_max_call_DP is not None:
        if args.hipstr_max_call_DP < 0:
            common.WARNING("--hipstr-max-call-DP must be >= 0")
            return False
        assert "DP" in format_fields
    if args.hipstr_min_call_DP is not None and args.hipstr_max_call_DP is not None:
        if args.hipstr_max_call_DP < args.hipstr_min_call_DP:
            common.WARNING("--hipstr-max-call-DP must be >= --hipstr-min-call-DP")
            return False
    if args.hipstr_min_call_Q is not None:
        if args.hipstr_min_call_Q < 0 or args.hipstr_min_call_Q > 1:
            common.WARNING("--hipstr-min-call-Q must be between 0 and 1")
            return False
        assert "Q" in format_fields
    return True

def CheckGangSTRFilters(format_fields, args):
    r"""Check GangSTR call-level filters

    Parameters
    ----------
    format_fields :
        The format fields used in this VCF
    args : argparse namespace
        Contains user arguments

    Returns
    -------
    checks : bool
        Set to True if all filters look ok.
        Set to False if filters are invalid
    """
    if args.gangstr_min_call_DP is not None:
        if args.gangstr_min_call_DP < 0:
            common.WARNING("--gangstr-min-call-DP must be >= 0")
            return False
        assert "DP" in format_fields
    if args.gangstr_max_call_DP is not None:
        if args.gangstr_max_call_DP < 0:
            common.WARNING("--gangstr-max-call-DP must be >= 0")
            return False
        assert "DP" in format_fields
    if args.gangstr_min_call_DP is not None and args.gangstr_max_call_DP is not None:
        if args.gangstr_max_call_DP < args.gangstr_min_call_DP:
            common.WARNING("--gangstr-max-call-DP must be >= --gangstr-min-call-DP")
            return False
    if args.gangstr_min_call_Q is not None:
        if args.gangstr_min_call_Q < 0 or args.gangstr_min_call_Q > 1:
            common.WARNING("--gangstr-min-call-Q must be between 0 and 1")
            return False
        assert "Q" in format_fields
    if args.gangstr_expansion_prob_het is not None:
        if args.gangstr_expansion_prob_het < 0 or args.gangstr_expansion_prob_het > 1:
            common.WARNING("--gangstr-expansion-prob-het must be between 0 and 1")
            return False
        assert "QEXP" in format_fields
    if args.gangstr_expansion_prob_hom is not None:
        if args.gangstr_expansion_prob_hom < 0 or args.gangstr_expansion_prob_hom > 1:
            common.WARNING("--gangstr-expansion-prob-hom must be between 0 and 1")
            return False
        assert "QEXP" in format_fields
    if args.gangstr_expansion_prob_total is not None:
        if args.gangstr_expansion_prob_total < 0 or args.gangstr_expansion_prob_total > 1:
            common.WARNING("--gangstr-expansion-prob-total must be between 0 and 1")
            return False
        assert "QEXP" in format_fields
    '''
    if args.gangstr_require_support is not None:
        if args.gangstr_require_support < 0:
            common.WARNING("--gangstr-require-support must be >= 0")
            return False
        if args.gangstr_require_support > 0 and args.gangstr_readlen is None:
            common.WARNING("Using --gangstr-require-support requires setting --gangstr-readlen")
            return False
        if args.gangstr_readlen is not None and args.gangstr_readlen < 20:
            common.WARNING("--gangstr-readlen must be an integer value >= 20")
            return False
        assert "ENCLREADS" in format_fields and "FLNKREADS" in format_fields and "RC" in format_fields
    '''
    return True

def CheckAdVNTRFilters(format_fields, args):
    r"""Check adVNTR call-level filters

    Parameters
    ----------
    format_fields :
        The format fields used in this VCF
    args : argparse namespace
        Contains user arguments

    Returns
    -------
    checks : bool
        Set to True if all filters look ok.
        Set to False if filters are invalid
    """
    if args.advntr_min_call_DP is not None:
        if args.advntr_min_call_DP < 0:
            common.WARNING("--advntr-min-call-DP must be >= 0")
            return False
        assert "DP" in format_fields
    if args.advntr_max_call_DP is not None:
        if args.advntr_max_call_DP < 0:
            common.WARNING("--advntr-max-call-DP must be >= 0")
            return False
        assert "DP" in format_fields
    if args.advntr_min_call_DP is not None and args.advntr_max_call_DP is not None:
        if args.advntr_max_call_DP < args.advntr_min_call_DP:
            common.WARNING("--advntr-max-call-DP must be >= --advntr-min-call-DP")
            return False
    if args.advntr_min_spanning is not None:
        if args.advntr_min_spanning < 0:
            common.WARNING("--advntr-min-spanning must be >=0")
            return False
        assert "SR" in format_fields
    if args.advntr_min_flanking is not None:
        if args.advntr_min_flanking < 0:
            common.WARNING("--advntr-min-flanking must be >=0")
            return False
        assert "FR" in format_fields
    if args.advntr_min_ML is not None:
        if args.advntr_min_ML < 0:
            common.WARNING("--advntr-min-ML must be >= 0")
            return False
        assert "ML" in format_fields
    return True

def CheckEHFilters(format_fields, args):
    r"""Check ExpansionHunter call-level filters

    Parameters
    ----------
    format_fields :
        The format fields used in this VCF
    args : argparse namespace
        Contains user arguments

    Returns
    -------
    checks : bool
        Set to True if all filters look ok.
        Set to False if filters are invalid
    """
    if args.eh_min_ADFL is not None:
        if args.eh_min_ADFL < 0:
            common.WARNING("--eh-min-ADFL must be >= 0")
            return False
        assert "ADFL" in format_fields
    if args.eh_min_ADIR is not None:
        if args.eh_min_ADIR < 0:
            common.WARNING("--eh-min-ADIR must be >= 0")
            return False
        assert "ADIR" in format_fields
    if args.eh_min_ADSP is not None:
        if args.eh_min_ADSP < 0:
            common.WARNING("--eh-min-ADSP must be >= 0")
            return False
        assert "ADSP" in format_fields
    if args.eh_min_call_LC is not None:
        if args.eh_min_call_LC < 0:
            common.WARNING("--eh-min-call-LC must be >= 0")
            return False
        assert "LC" in format_fields
    if args.eh_max_call_LC is not None:
        if args.eh_max_call_LC < 0:
            common.WARNING("--eh-max-call-LC must be >= 0")
            return False
        assert "LC" in format_fields
    if args.eh_min_call_LC is not None and args.eh_max_call_LC is not None:
        if args.eh_max_call_LC < args.eh_min_call_LC:
            common.WARNING("--eh-max-call-LC must be >= --eh-min-call-LC")
            return False
    return True

def CheckPopSTRFilters(format_fields, args):
    r"""Check PopSTR call-level filters

    Parameters
    ----------
    format_fields :
        The format fields used in this VCF
    args : argparse namespace
        Contains user arguments

    Returns
    -------
    checks : bool
        Set to True if all filters look ok.
        Set to False if filters are invalid
    """
    if args.popstr_min_call_DP is not None:
        if args.popstr_min_call_DP < 0:
            common.WARNING("--popstr-min-call-DP must be >= 0")
            return False
        assert "DP" in format_fields
    if args.popstr_max_call_DP is not None:
        if args.popstr_max_call_DP < 0:
            common.WARNING("--popstr-max-call-DP must be >= 0")
            return False
        assert "DP" in format_fields
    if args.popstr_min_call_DP is not None and args.popstr_max_call_DP is not None:
        if args.popstr_max_call_DP < args.popstr_min_call_DP:
            common.WARNING("--popstr-max-call-DP must be >= --popstr-min-call-DP")
            return False
    if args.popstr_require_support is not None:
        if args.popstr_require_support < 0:
            common.WARNING("--popstr-require-support must be >= 0")
            return False
        assert "AD" in format_fields
    return True

def CheckFilters(format_fields: Set[str],
                 args: argparse.Namespace,
                 vcftype: trh.VcfTypes):
    r"""Perform checks on user input for filters.

    Assert that user input matches the type of the input vcf.

    Parameters
    ----------
    format_fields :
        The format fields used in this VCF
    args :
        Contains user arguments
    vcftype :
        Specifies which tool this VCF came from.

    Returns
    -------
    checks : bool
        Set to True if all filters look ok.
        Set to False if filters are invalid
    """
    if not CheckLocusFilters(args, vcftype):
        return False

    # Check HipSTR specific filters
    if args.hipstr_max_call_flank_indel is not None or \
       args.hipstr_max_call_stutter is not None or \
       args.hipstr_min_supp_reads is not None or \
       args.hipstr_min_call_DP is not None or \
       args.hipstr_max_call_DP is not None or \
       args.hipstr_min_call_Q is not None or \
       args.hipstr_min_call_allele_bias is not None or \
       args.hipstr_min_call_strand_bias is not None:
        if vcftype != trh.VcfTypes["hipstr"]:
            common.WARNING("HipSTR options can only be applied to HipSTR VCFs")
            return False
        else:
            if not CheckHipSTRFilters(format_fields, args):
                return False

    # Check GangSTR specific filters
    if args.gangstr_min_call_DP is not None or \
       args.gangstr_max_call_DP is not None or \
       args.gangstr_min_call_Q is not None or \
       args.gangstr_expansion_prob_het is not None or \
       args.gangstr_expansion_prob_hom is not None or \
       args.gangstr_expansion_prob_total is not None or \
       args.gangstr_filter_span_only or \
       args.gangstr_filter_spanbound_only or \
       args.gangstr_filter_badCI or \
       args.gangstr_readlen is not None:
        # args.gangstr_require_support is not None or \
        if vcftype != trh.VcfTypes["gangstr"]:
            common.WARNING("GangSTR options can only be applied to GangSTR VCFs")
            return False
        else:
            if not CheckGangSTRFilters(format_fields, args):
                return False

    # Check adVNTR specific filters
    if args.advntr_min_call_DP is not None or \
       args.advntr_max_call_DP is not None or \
       args.advntr_min_spanning is not None or \
       args.advntr_min_flanking is not None or \
       args.advntr_min_ML is not None:
        if vcftype != trh.VcfTypes["advntr"]:
            common.WARNING("adVNTR options can only be applied to adVNTR VCFs")
            return False
        else:
            if not CheckAdVNTRFilters(format_fields, args):
                return False

    # Check EH specific filters
    if args.eh_min_ADFL is not None or \
       args.eh_min_ADIR is not None or \
       args.eh_min_ADSP is not None or \
       args.eh_min_call_LC is not None or \
       args.eh_max_call_LC is not None:
        if vcftype != trh.VcfTypes["eh"]:
            common.WARNING("ExpansionHunter options can only be applied to ExpansionHunter VCFs")
            return False
        else:  # pragma: no cover
            if not CheckEHFilters(format_fields, args):  # pragma: no cover
                return False  # pragma: no cover

    # Check popSTR specific filters
    if args.popstr_min_call_DP is not None or \
       args.popstr_max_call_DP is not None or \
       args.popstr_require_support is not None:
        if vcftype != trh.VcfTypes["popstr"]:
            common.WARNING("popSTR options can only be applied to popSTR VCFs")
            return False
        else:
            if not CheckPopSTRFilters(format_fields, args):
                return False
    return True

def WriteLocLog(loc_info, fname):
    r"""Write locus-level features to log file

    Parameters
    ----------
    loc_info : dict of str->value
       Dictionary containing locus-level stats.
       Must have at least keys: 'totalcalls', 'PASS'
    fname : str
       Output log filename

    Returns
    -------
    success : bool
       Set to true if outputting the log was successful
    """
    f = open(fname, "w")
    keys = list(loc_info.keys())
    assert "totalcalls" in keys and "PASS" in keys
    keys.remove("totalcalls")
    if loc_info["PASS"] == 0:
        callrate = 0
    else:
        callrate = float(loc_info["totalcalls"])/loc_info["PASS"]
    f.write("MeanSamplesPerPassingSTR\t%s\n"%callrate)
    for k in keys:
        f.write("FILTER:%s\t%s\n"%(k, loc_info[k]))
    f.close()
    return True

def WriteSampLog(sample_info: Dict[str, np.ndarray],
                 sample_names: List[str],
                 fname: str):
    r"""Write sample-level features to log file.

    Parameters
    ----------
    sample_info :
        Mapping from statistic name to 1D array of values per sample
    sample_names:
        List of sample names, same length as above arrays
    fname : str
        Output filename
    """
    header = ["sample"]
    header.extend(sample_info.keys())
    header[header.index('totaldp')] = 'meanDP'
    with open(fname, "w") as f:
        f.write("\t".join(header)+"\n")
        # write Total row
        f.write("Total\t")
        numcalls = np.sum(sample_info['numcalls'])
        f.write(str(numcalls))
        f.write('\t')
        if numcalls > 0:
            f.write(str(np.sum(sample_info['totaldp'])/numcalls))
        else:
            f.write("0")

        for filt_counts in itertools.islice(sample_info.values(), 2, None):
            f.write("\t")
            f.write(str(np.sum(filt_counts)))
        f.write("\n")

        # write a row for each sample
        for samp_idx, s in enumerate(sample_names):
            f.write(s)
            f.write("\t")

            numcalls = sample_info["numcalls"][samp_idx]
            f.write(str(numcalls))
            f.write("\t")

            if numcalls > 0:
                f.write(str(sample_info["totaldp"][samp_idx]*1.0/numcalls))
            else:
                f.write("0")

            for filt_counts in itertools.islice(sample_info.values(), 2, None):
                f.write("\t")
                f.write(str(filt_counts[samp_idx]))
            f.write("\n")


def GetAllCallFilters(call_filters):
    r"""List all possible call filters

    Parameters
    ----------
    call_filters : list of filters.Reason
        List of all call-level filters

    Returns
    -------
    reasons : list of str
        A list of call-level filter reason strings
    """
    reasons = []
    for filt in call_filters:
        reasons.append(filt.name)
    return reasons


_NOCALL_INT_FORMAT_VAL = -2147483648


def ApplyCallFilters(record: trh.TRRecord,
                     call_filters: List[filters.FilterBase],
                     sample_info: Dict[str, np.ndarray],
                     sample_names: List[str]) -> trh.TRRecord:
    r"""Apply call-level filters to a record.

    Returns a TRRecord object with the FILTER (or DUMPSTR_FILTER)
    format field updated for each sample.
    Also updates sample_info with sample level stats

    Parameters
    ----------
    record :
       The record to apply filters to.
       Note: once this method has been run, this object
       will be in an inconsistent state. All further use
       should be directed towards the returned TRRecord object.
    call_filters :
       List of call filters to apply
    sample_info :
       Dictionary of sample stats to keep updated,
       from name of filter to array of length nsamples
       which counts the number of times that filter has been
       applied to each sample across all loci
    sample_names:
        Names of all the samples in the vcf. Used for formatting
        error messages.

    Returns
    -------
    trh.TRRecord
        A reference to the same underlying cyvcf2.Variant object,
        which has now been modified to contain all the new call-level
        filters.
    """
    # this array will contain the text to append in the Filter FORMAT
    # field for each sample
    all_filter_text = np.empty((record.GetNumSamples()), 'U4')
    nocalls = ~record.GetCalledSamples()

    for filt in call_filters:
        filt_output = filt(record)
        # This will throw a TypeError if passed a non numeric
        # array. Will need better logic here if we decide to create
        # call level filters which return nonnumeric output
        nans = np.isnan(filt_output)
        if np.all(nans):
            continue
        sample_info[filt.name] += np.logical_and(~nans, ~nocalls)
        # append ',<filter_name><value_that_triggered_fitler>' to each
        # call that has a filter applied to it
        filt_output_text = np.char.mod('%g', filt_output)
        filt_output_text = np.char.add('_', filt_output_text)
        filt_output_text = np.char.add(filt.name, filt_output_text)
        filt_output_text[nans] = '' # don't add text to calls that haven't been filtered
        # only append a ',' if this is the second (or more) filter applied
        # to this call
        not_first_filter = np.logical_and(~nans, all_filter_text != '')
        all_filter_text[not_first_filter] = \
            np.char.add(all_filter_text[not_first_filter], ',')
        all_filter_text = np.char.add(all_filter_text, filt_output_text)

    # append NOCALL to each sample that has not been called
    if np.any(nocalls):
        nocall_text = np.empty((nocalls.shape[0]), dtype='U6')
        nocall_text[nocalls] = 'NOCALL'
        # if there was already a no call, leave an empty filter
        # field instead of NOCALL
        all_filter_text[nocalls] = ''
        all_filter_text = np.char.add(all_filter_text, nocall_text)
    all_filter_text[all_filter_text == ''] = 'PASS'
    record.vcfrecord.set_format('FILTER', np.char.encode(all_filter_text))

    extant_calls = all_filter_text == 'PASS'
    sample_info['numcalls'] += extant_calls
    dp_vals = None
    try:
        dp_vals = record.format['DP']
    except KeyError:
        dp_vals = record.format['LC']
    except KeyError:
        pass
    if dp_vals is not None:
        dp_vals = dp_vals.reshape(-1)
        negative_dp_called_samples = np.logical_and(np.logical_and(
                dp_vals < 0, dp_vals != _NOCALL_INT_FORMAT_VAL), extant_calls)
        if np.any(negative_dp_called_samples):
            raise ValueError(
                "The following samples have calls but negative DP values "
                "at chromosome {} pos {}: {}".format(
                    record.chrom, record.pos,
                    str(sample_names[negative_dp_called_samples]))
            )
        accumulate_dp_samples = np.logical_and(extant_calls, dp_vals > 0)
        sample_info['totaldp'][accumulate_dp_samples] += \
            dp_vals[accumulate_dp_samples]
        sample_info['totaldp'][np.logical_and(extant_calls,
            dp_vals == _NOCALL_INT_FORMAT_VAL)] = np.nan
    else:
        sample_info['totaldp'][:] = np.nan

    filtered_samples = np.logical_and(
            all_filter_text != 'PASS', all_filter_text != 'NOCALL'
    )
    if not np.any(filtered_samples):
        return record #nothing else to do
    
    # mask the filtered genotypes
    ploidy = record.GetMaxPloidy()
    for idx in filtered_samples.nonzero()[0]:
        record.vcfrecord.genotypes[idx] = [-1]*ploidy + [False]
    # This line isn't actually a no-op, see docs here:
    # https://github.com/brentp/cyvcf2/blob/master/docs/source/writing.rst
    record.vcfrecord.genotypes = record.vcfrecord.genotypes

    # mask all other format fields
    for field in record.format:
        if field == 'GT' or field == 'FILTER':
            continue
        vals = record.format[field]
        # null the filtered values
        # different null value for different array types
        if vals.dtype.kind == 'U':
            vals[filtered_samples] = '.'
            vals = np.char.encode(vals)
        elif vals.dtype.kind == 'f':
            vals[filtered_samples] = np.nan
        elif vals.dtype.kind == 'i':
            vals[filtered_samples] = _NOCALL_INT_FORMAT_VAL
        else:
            raise ValueError("Found an unexpected format dtype for"
                             " format field " + field)
        record.vcfrecord.set_format(field, vals)

    # rebuild the TRRecord with the newly modified cyvcf2 vcfrecord
    if record.HasFabricatedAltAlleles():
        alt_alleles = None
        alt_allele_lengths = record.alt_allele_lengths
    else:
        alt_alleles = record.alt_alleles
        alt_allele_lengths = None
    if record.HasFabricatedRefAllele():
        ref_allele = None
        ref_allele_length = record.ref_allele_length
    else:
        ref_allele = record.ref_allele
        ref_allele_length = None
    if record.HasFullStringGenotypes():
        trimmed_end_pos = record.trimmed_end_pos
        trimmed_pos = record.trimmed_pos
    else:
        trimmed_end_pos = None
        trimmed_pos = None


    out_record = trh.TRRecord(
        record.vcfrecord,
        ref_allele,
        alt_alleles,
        record.motif,
        record.record_id,
        record.quality_field,
        full_alleles=record.full_alleles,
        trimmed_pos=trimmed_pos,
        trimmed_end_pos=trimmed_end_pos,
        ref_allele_length=ref_allele_length,
        alt_allele_lengths=alt_allele_lengths,
        quality_score_transform=record.quality_score_transform
    )
    return out_record


def BuildCallFilters(args):
    r"""Build list of locus-level filters to include

    Parameters
    ----------
    args : argparse namespace
       User input arguments used to decide on filters

    Returns
    -------
    filter_list : list of filters.Filter
       List of call-level filters to apply
    """
    filter_list = []

    # HipSTR call-level filters
    if args.hipstr_max_call_flank_indel is not None:
        filter_list.append(filters.HipSTRCallFlankIndels(args.hipstr_max_call_flank_indel))
    if args.hipstr_max_call_stutter is not None:
        filter_list.append(filters.HipSTRCallStutter(args.hipstr_max_call_stutter))
    if args.hipstr_min_supp_reads is not None:
        filter_list.append(filters.HipSTRCallMinSuppReads(args.hipstr_min_supp_reads))
    if args.hipstr_min_call_DP is not None:
        filter_list.append(filters.CallFilterMinValue("HipSTRCallMinDepth", "DP", args.hipstr_min_call_DP))
    if args.hipstr_max_call_DP is not None:
        filter_list.append(filters.CallFilterMaxValue("HipSTRCallMaxDepth", "DP", args.hipstr_max_call_DP))
    if args.hipstr_min_call_Q is not None:
        filter_list.append(filters.CallFilterMinValue("HipSTRCallMinQ", "Q", args.hipstr_min_call_Q))
    if args.hipstr_min_call_allele_bias is not None:
        filter_list.append(filters.CallFilterMinValue("HipSTRCallMinAlleleBias", "AB", args.hipstr_min_call_allele_bias))
    if args.hipstr_min_call_strand_bias is not None:
        filter_list.append(filters.CallFilterMinValue("HipSTRCallMinStrandBias", "FS", args.hipstr_min_call_strand_bias))

    # GangSTR call-level filters
    if args.gangstr_min_call_DP is not None:
        filter_list.append(filters.CallFilterMinValue("GangSTRCallMinDepth", "DP", args.gangstr_min_call_DP))
    if args.gangstr_max_call_DP is not None:
        filter_list.append(filters.CallFilterMaxValue("GangSTRCallMaxDepth", "DP", args.gangstr_max_call_DP))
    if args.gangstr_min_call_Q is not None:
        filter_list.append(filters.CallFilterMinValue("GangSTRCallMinQ", "Q", args.gangstr_min_call_Q))
    if args.gangstr_expansion_prob_het is not None:
        filter_list.append(filters.GangSTRCallExpansionProbHet(args.gangstr_expansion_prob_het))
    if args.gangstr_expansion_prob_hom is not None:
        filter_list.append(filters.GangSTRCallExpansionProbHom(args.gangstr_expansion_prob_hom))
    if args.gangstr_expansion_prob_total is not None:
        filter_list.append(filters.GangSTRCallExpansionProbTotal(args.gangstr_expansion_prob_total))
    if args.gangstr_filter_span_only:
        filter_list.append(filters.GangSTRCallSpanOnly())
    if args.gangstr_filter_spanbound_only:
        filter_list.append(filters.GangSTRCallSpanBoundOnly())
    if args.gangstr_filter_badCI:
        filter_list.append(filters.GangSTRCallBadCI())
    '''
    if args.gangstr_require_support is not None:
        filter_list.append(filters.GangSTRCallRequireSupport(args.gangstr_require_support, args.gangstr_readlen))
    '''

    # adVNTR call-level filters
    if args.advntr_min_call_DP is not None:
        filter_list.append(filters.CallFilterMinValue("AdVNTRCallMinDepth", "DP", args.advntr_min_call_DP))
    if args.advntr_max_call_DP is not None:
        filter_list.append(filters.CallFilterMaxValue("AdVNTRCallMaxDepth", "DP", args.advntr_max_call_DP))
    if args.advntr_min_spanning is not None:
        filter_list.append(filters.CallFilterMinValue("AdVNTRCallMinSpanning", "SR", args.advntr_min_spanning))
    if args.advntr_min_flanking is not None:
        filter_list.append(filters.CallFilterMinValue("AdVNTRCallMinFlanking", "FR", args.advntr_min_flanking))
    if args.advntr_min_ML is not None:
        filter_list.append(filters.CallFilterMinValue("AdVNTRCallMinML", "ML", args.advntr_min_ML))

    # EH call-level filters
    if args.eh_min_call_LC is not None:
        filter_list.append(filters.CallFilterMinValue("EHCallMinDepth", "LC", args.eh_min_call_LC))  # pragma: no cover
    if args.eh_max_call_LC is not None:
        filter_list.append(filters.CallFilterMaxValue("EHCallMaxDepth", "LC", args.eh_max_call_LC))  # pragma: no cover
    if args.eh_min_ADFL is not None:
        filter_list.append(filters.CallFilterMinValue("EHCallMinADFL", "ADFL", args.eh_min_ADFL))  # pragma: no cover
    if args.eh_min_ADIR is not None:
        filter_list.append(filters.CallFilterMinValue("EHCallMinADFL", "ADIR", args.eh_min_ADIR))  # pragma: no cover
    if args.eh_min_ADSP is not None:
        filter_list.append(filters.CallFilterMinValue("EHCallMinADSP", "ADSP", args.eh_min_ADSP)) # pragma: no cover

    # popSTR call-level filters
    if args.popstr_min_call_DP is not None:
        filter_list.append(filters.CallFilterMinValue("PopSTRMinCallDepth", "DP", args.popstr_min_call_DP))
    if args.popstr_max_call_DP is not None:
        filter_list.append(filters.CallFilterMaxValue("PopSTRMaxCallDepth", "DP", args.popstr_max_call_DP))
    if args.popstr_require_support is not None:
        filter_list.append(filters.PopSTRCallRequireSupport(args.popstr_require_support))
    return filter_list

def BuildLocusFilters(args):
    r"""Build list of locus-level filters to include.

    These filters should in general not be tool specific

    Parameters
    ---------
    args : argparse namespace
       User input arguments used to decide on filters

    Returns
    -------
    filter_list : list of filters.Filter
       List of locus-level filters
    """
    filter_list = []
    if args.min_locus_callrate is not None:
        filter_list.append(filters.Filter_MinLocusCallrate(args.min_locus_callrate))
    if args.min_locus_hwep is not None:
        filter_list.append(filters.Filter_MinLocusHWEP(args.min_locus_hwep,
                                                       args.use_length))
    if args.min_locus_het is not None:
        filter_list.append(filters.Filter_MinLocusHet(args.min_locus_het,
                                                      args.use_length))
    if args.max_locus_het is not None:
        filter_list.append(filters.Filter_MaxLocusHet(args.max_locus_het,
                                                      args.use_length))
    if args.filter_hrun:
        filter_list.append(filters.Filter_LocusHrun())
    if args.filter_regions is not None:
        filter_region_files = args.filter_regions.split(",")
        if args.filter_regions_names is not None:
            filter_region_names = args.filter_regions_names.split(",")
        else: filter_region_names = ['FILTER' + str(item) for item in list(range(len(filter_region_files)))]
        for i in range(len(filter_region_names)):
            region_filter = filters.create_region_filter(filter_region_names[i], filter_region_files[i])
            if region_filter is not None:
                filter_list.append(region_filter)
            else:
                raise ValueError('Could not load regions file: {}'.format(filter_region_files[i]))
    return filter_list

def ApplyLocusFilters(record: trh.TRRecord,
                      locus_filters: List[filters.FilterBase],
                      loc_info: Dict[str, int],
                      drop_filtered: bool) -> bool:
    """Apply locus-level filters to a record.

    If not drop_filtered, then the input record's FILTER
    field is set as either PASS or the names of the filters which filtered it.

    Parameters
    ----------
    record :
       The record to apply filters to.
    call_filters :
       List of locus filters to apply
    loc_info :
       Dictionary of locus stats to keep updated,
       from name of filter to count of times the filter has been applied
    drop_filtered :
        Whether or not filtered loci should be written to or dropped from
        the output vcf.

    Returns
    -------
    locus_filtered: bool
        True if this locus was filtered
    """

    filtered = False
    for filt in locus_filters:
        if filt(record) is None:
            continue
        loc_info[filt.filter_name()] += 1
        if not drop_filtered:
            if not filtered:
                record.vcfrecord.FILTER = filt.filter_name()
            else:
                record.vcfrecord.FILTER += ';' + filt.filter_name()
        filtered = True

    n_samples_called = np.sum(record.GetCalledSamples())
    if n_samples_called == 0:
        loc_info['NO_CALLS_REMAINING'] += 1
        if not drop_filtered:
            if not filtered:
                record.vcfrecord.FILTER = 'NO_CALLS_REMAINING'
            else:
                record.vcfrecord.FILTER += ';' + 'NO_CALLS_REMAINING'
        filtered = True

    if not filtered:
        if not drop_filtered:
            record.vcfrecord.FILTER = "PASS"
        loc_info["PASS"] += 1
        loc_info["totalcalls"] += n_samples_called

    return filtered


def getargs(): # pragma: no cover
    parser = argparse.ArgumentParser(
        __doc__,
        formatter_class=utils.ArgumentDefaultsHelpFormatter
    )
    # In/out are always relevant
    inout_group = parser.add_argument_group("Input/output")
    inout_group.add_argument("--vcf", help="Input STR VCF file", type=str, required=True)
    inout_group.add_argument("--out", help="Prefix for output files", type=str, required=True)
    inout_group.add_argument("--zip", help="Produce a bgzipped and tabix indexed output VCF", action="store_true")
    inout_group.add_argument("--vcftype", help="Options=%s"%[str(item) for item in trh.VcfTypes.__members__], type=str, default="auto")

    # Locus-level filters are not specific to any tool
    locus_group = parser.add_argument_group("Locus-level filters (tool agnostic)")
    locus_group.add_argument("--min-locus-callrate", help="Minimum locus call rate", type=float)
    locus_group.add_argument("--min-locus-hwep", help="Filter loci failing HWE at this p-value threshold", type=float)
    locus_group.add_argument("--min-locus-het", help="Minimum locus heterozygosity", type=float)
    locus_group.add_argument("--max-locus-het", help="Maximum locus heterozygosity", type=float)
    locus_group.add_argument("--use-length", help="Calculate per-locus stats (het, HWE) collapsing alleles by length", action="store_true")
    locus_group.add_argument("--filter-regions", help="Comma-separated list of BED files of regions to filter. Must be bgzipped and tabix indexed", type=str)
    locus_group.add_argument("--filter-regions-names", help="Comma-separated list of filter names for each BED filter file", type=str)
    locus_group.add_argument("--filter-hrun", help="Filter STRs with long homopolymer runs.", action="store_true")
    locus_group.add_argument("--drop-filtered", help="Drop filtered records from output", action="store_true")

    ###### Tool specific filters #####
    hipstr_call_group = parser.add_argument_group("Call-level filters specific to HipSTR output")
    hipstr_call_group.add_argument("--hipstr-max-call-flank-indel", help="Maximum call flank indel rate", type=float)
    hipstr_call_group.add_argument("--hipstr-max-call-stutter", help="Maximum call stutter rate", type=float)
    hipstr_call_group.add_argument("--hipstr-min-supp-reads", help="Minimum supporting reads for each allele", type=int)
    hipstr_call_group.add_argument("--hipstr-min-call-DP", help="Minimum call coverage", type=int)
    hipstr_call_group.add_argument("--hipstr-max-call-DP", help="Maximum call coverage", type=int)
    hipstr_call_group.add_argument("--hipstr-min-call-Q", help="Minimum call quality score", type=float)
    hipstr_call_group.add_argument("--hipstr-min-call-allele-bias", help="Minimum call allele bias (from AB format field)", type=float)
    hipstr_call_group.add_argument("--hipstr-min-call-strand-bias", help="Minimum call strand bias (from FS format field)", type=float)

    gangstr_call_group = parser.add_argument_group("Call-level filters specific to GangSTR output")
    gangstr_call_group.add_argument("--gangstr-min-call-DP", help="Minimum call coverage", type=int)
    gangstr_call_group.add_argument("--gangstr-max-call-DP", help="Maximum call coverage", type=int)
    gangstr_call_group.add_argument("--gangstr-min-call-Q", help="Minimum call quality score", type=float)
    gangstr_call_group.add_argument("--gangstr-expansion-prob-het", help="Expansion prob-value threshold. Filters calls with probability of heterozygous expansion less than this", type=float)
    gangstr_call_group.add_argument("--gangstr-expansion-prob-hom", help="Expansion prob-value threshold. Filters calls with probability of homozygous expansion less than this", type=float)
    gangstr_call_group.add_argument("--gangstr-expansion-prob-total", help="Expansion prob-value threshold. Filters calls with probability of total expansion less than this", type=float)
    gangstr_call_group.add_argument("--gangstr-filter-span-only", help="Filter out all calls that only have spanning read support", action="store_true")
    gangstr_call_group.add_argument("--gangstr-filter-spanbound-only", help="Filter out all reads except spanning and bounding", action="store_true")
    gangstr_call_group.add_argument("--gangstr-filter-badCI", help="Filter regions where the ML estimate is not in the CI", action="store_true")
    # gangstr_call_group.add_argument("--gangstr-require-support", help="Require each allele call to have at least n supporting reads", type=int)
    gangstr_call_group.add_argument("--gangstr-readlen", help="Read length used (bp). Required if using --require-support", type=int)

    advntr_call_group = parser.add_argument_group("Call-level filters specific to adVNTR output")
    advntr_call_group.add_argument("--advntr-min-call-DP", help="Minimum call coverage", type=int)
    advntr_call_group.add_argument("--advntr-max-call-DP", help="Maximum call coverage", type=int)
    advntr_call_group.add_argument("--advntr-min-spanning", help="Minimum spanning read count (SR field)", type=int)
    advntr_call_group.add_argument("--advntr-min-flanking", help="Minimum flanking read count (FR field)", type=int)
    advntr_call_group.add_argument("--advntr-min-ML", help="Minimum value of maximum likelihood (ML field)", type=float)

    eh_call_group = parser.add_argument_group("Call-level filters specific to ExpansionHunter output")
    eh_call_group.add_argument("--eh-min-ADFL", help="Minimum number of flanking reads consistent with the allele", type=int)
    eh_call_group.add_argument("--eh-min-ADIR", help="Minimum number of in-repeat reads consistent with the allele", type=int)
    eh_call_group.add_argument("--eh-min-ADSP", help="Minimum number of spanning reads consistent with the allele", type=int)
    eh_call_group.add_argument("--eh-min-call-LC", help="Minimum call coverage", type=int)
    eh_call_group.add_argument("--eh-max-call-LC", help="Maximum call coverage", type=int)
    # TODO: add SO field filter. After clarifying possible formats it can take

    popstr_call_group = parser.add_argument_group("Call-level filters specific to PopSTR output")
    popstr_call_group.add_argument("--popstr-min-call-DP", help="Minimum call coverage", type=int)
    popstr_call_group.add_argument("--popstr-max-call-DP", help="Maximum call coverage", type=int)
    popstr_call_group.add_argument("--popstr-require-support", help="Require each allele call to have at least n supporting reads", type=int)

    # Debugging options
    debug_group = parser.add_argument_group("Debugging parameters")
    debug_group.add_argument("--num-records", help="Only process this many records", type=int)
    debug_group.add_argument("--die-on-warning", help="Quit if a record can't be parsed", action="store_true")
    debug_group.add_argument("--verbose", help="Print out extra info", action="store_true")
    # Version option
    ver_group = parser.add_argument_group("Version")
    ver_group.add_argument("--version", action="version", version = '{version}'.format(version=__version__))
    args = parser.parse_args()
    return args

def main(args):
    # Load VCF file
    invcf = utils.LoadSingleReader(args.vcf, checkgz = False)
    if invcf is None:
        return 1

    if not os.path.exists(os.path.dirname(os.path.abspath(args.out))):
        common.WARNING("Error: The directory which contains the output location {} does"
                       " not exist".format(args.out))
        return 1

    if os.path.isdir(args.out + ".vcf"):
        common.WARNING("Error: The output location {} is a "
                       "directory".format(args.out))
        return 1

    if args.out[-1] in {'.', '/'}:
        common.WARNING("Output prefix must not end in '/' or '.'")
        return 1

    # Set up record harmonizer and infer VCF type
    harmonizer = trh.TRRecordHarmonizer(invcf, args.vcftype)
    vcftype = harmonizer.vcftype

    format_fields = {}
    info_fields = {}
    preexisting_filter_fields = {}
    contig_fields = set()

    for header_line in invcf.header_iter():
        if header_line['HeaderType'] == 'INFO':
            info_fields[header_line['ID']] = header_line
        elif header_line['HeaderType'] == 'FORMAT':
            format_fields[header_line['ID']] = header_line
        elif header_line['HeaderType'] == 'FILTER':
            preexisting_filter_fields[header_line['ID']] = header_line
        elif header_line['HeaderType'].lower() == 'contig':
            contig_fields.add(header_line['ID'])

    # Check filters all make sense
    if not CheckFilters(format_fields, args, vcftype): return 1

    field_issues = False
    field_issue_statement = (
        "Error: The {} field '{}' is present in the input "
        "VCF and doesn't have the expected Type and Number "
        "so it can't be worked with. Please "
        "use 'bcftools annotate --rename-annots' or another equivalent tool to "
        "rename or remove the field and then rerun dumpSTR. "
        "(--rename-annots is a flag available in the development version of "
        "bcftools which can be installed from "
        "https://samtools.github.io/bcftools/) "
        "(You can pipe the output of that command into dumpSTR if you wish "
        "to avoid writing another file to disk)"
    )

    if 'FILTER' not in format_fields:
        invcf.add_format_to_header({
            'ID': 'FILTER',
            'Description': 'call-level filters that have been applied',
            'Type': 'String',
            'Number': 1
        })
    else:
        if (format_fields['FILTER']['Type'] != 'String' or
            format_fields['FILTER']['Number'] != '1'):
            field_issues = True
            common.WARNING(field_issue_statement.format('format', 'FILTER'))

    ac_description = 'Alternate allele counts'
    if 'AC' not in info_fields:
        invcf.add_info_to_header({
            'ID': 'AC',
            'Description': ac_description,
            'Type': 'Integer',
            'Number': 'A'
        })
    else:
        if (info_fields['AC']['Type'] != 'Integer' or
            info_fields['AC']['Number'] != 'A'):
            field_issues = True
            common.WARNING(field_issue_statement.format('info', 'AC'))
        elif info_fields['AC']['Description'] != ac_description:
            common.WARNING("Overwriting the preexisting info AC field")

    refac_description = 'Reference allele count'
    if 'REFAC' not in info_fields:
        invcf.add_info_to_header({
            'ID': 'REFAC',
            'Description': refac_description,
            'Type': 'Integer',
            'Number': 1
        })
    else:
        if (info_fields['REFAC']['Type'] != 'Integer' or
            info_fields['REFAC']['Number'] != '1'):
            field_issues = True
            common.WARNING(field_issue_statement.format('info', 'REFAC'))
        elif info_fields['REFAC']['Description'] != refac_description:
            common.WARNING("Overwriting the preexisting info REFAC field")

    het_description = 'Heterozygosity'
    if 'HET' not in info_fields:
        invcf.add_info_to_header({
           'ID': 'HET',
           'Description': het_description,
           'Type': 'Float',
           'Number': 1
       })
    else:
        if (info_fields['HET']['Type'] != 'Float' or
            info_fields['HET']['Number'] != '1'):
            field_issues = True
            common.WARNING(field_issue_statement.format('info', 'HET'))
        elif info_fields['HET']['Description'] != het_description:
            common.WARNING("Overwriting the preexisting info HET field")

    hwep_description = 'HWE p-value for obs. vs. exp het rate'
    if 'HWEP' not in info_fields:
        invcf.add_info_to_header({
            'ID': 'HWEP',
            'Description':  hwep_description,
            'Type': 'Float',
            'Number': 1
        })
    else:
        if (info_fields['HWEP']['Type'] != 'Float' or
            info_fields['HWEP']['Number'] != '1'):
            field_issues = True
            common.WARNING(field_issue_statement.format('info', 'HWEP'))
        elif info_fields['HWEP']['Description'] != hwep_description:
            common.WARNING("Overwriting the preexisting info HWEP field")

    hrun_description = 'Length of longest homopolymer run'
    if 'HRUN' not in info_fields:
        invcf.add_info_to_header({
            'ID': 'HRUN',
            'Description':  hrun_description,
            'Type': 'Integer',
            'Number': 1
        })
    else:
        if (info_fields['HRUN']['Type'] != 'Integer' or
            info_fields['HRUN']['Number'] != '1'):
            field_issues = True
            common.WARNING(field_issue_statement.format('info', 'HRUN'))
        elif info_fields['HRUN']['Description'] != hrun_description:
            common.WARNING("Overwriting the preexisting info HRUN field")

    if field_issues:
        return 1

    # Set up locus-level filter list
    invcf.add_filter_to_header({
        "ID": "NO_CALLS_REMAINING",
        "Description": ("All calls at this locus were already nocalls or were individually "
                "filtered before the locus level filters were applied.")
    })
    try:
        locus_filters = BuildLocusFilters(args)
    except ValueError:
        return 1
    for f in locus_filters:
        if f.filter_name() not in preexisting_filter_fields:
            invcf.add_filter_to_header({
                "ID": f.filter_name(),
                "Description": f.description()
            })
        elif preexisting_filter_fields[f.filter_name()]['Description'] != f.description():
            common.WARNING("Using locus level filter " + f.filter_name() +
                           "which has the same name as a FILTER field that "
                           "already exists in the input VCF. The filters DumpSTR "
                           "writes to the output with this name will possibly "
                           "have different meanings than the filters with "
                           "the name that are already present.")

    # Set up call-level filters
    call_filters = BuildCallFilters(args)

    # Set up output files
    if args.zip:
        suffix = '.vcf.gz'
    else:
        suffix = '.vcf'
    outvcf = MakeWriter(args.out + suffix, invcf, " ".join(sys.argv))
    if outvcf is None: return 1

    # Set up sample info
    # use an ordered dict so we write out the filters in samplog.tab
    # in a reproducible order
    sample_info = collections.OrderedDict()
    # insert 'numcalls' and 'totaldp' in this order so that
    # they are printed out in the sample log in this order
    sample_info['numcalls'] = np.zeros((len(invcf.samples)), dtype=int)
    # use dtype float to allow for nan values
    sample_info['totaldp'] = np.zeros((len(invcf.samples)), dtype=float)

    for filter_name in GetAllCallFilters(call_filters):
        sample_info[filter_name] = np.zeros((len(invcf.samples)), dtype=int)

    # Set up locus info
    # use an ordered dict so we write out the filters in loclog.tab
    # in a reproducible order
    loc_info = collections.OrderedDict()
    loc_info["totalcalls"] = 0
    loc_info["PASS"] = 0
    loc_info["NO_CALLS_REMAINING"] = 0
    for filt in locus_filters: loc_info[filt.filter_name()] = 0

    # Go through each record
    record_counter = 0
    start = time.time()
    last_notification = start
    print("Starting filtering", flush=True)
    while True:
        try:
            record = next(harmonizer)
        except StopIteration: break
        except TypeError as te:
            message = te.args[0]
            if 'is not a ' in message and 'record' in message:
                common.WARNING("Could not parse VCF.\n" + message)
                return 1
            else:
                raise te
        if args.verbose:
            common.MSG("Processing %s:%s"%(record.chrom, record.pos))
        record_counter += 1
        if args.num_records is not None and record_counter > args.num_records: break

        # Call-level filters
        record = ApplyCallFilters(record, call_filters, sample_info, invcf.samples)

        # Locus-level filters
        locus_filtered = ApplyLocusFilters(record, locus_filters, loc_info, args.drop_filtered)

        if args.drop_filtered and locus_filtered:
            if time.time() - last_notification > 5:
                last_notification = time.time()
                sys.stdout.write('\033[2K\033[1G')
                print("Processed {} loci. Time per locus: {:0.3g}".format(
                    record_counter, (time.time() - start)/record_counter), end='\r',
                    flush=True)
            continue

        # Recalculate locus-level INFO fields
        # TODO the filters have already calcualted these values, don't
        # repeat the calculation here
        if record.HasFullStringGenotypes():
            record.vcfrecord.INFO['HRUN'] = \
                utils.GetHomopolymerRun(record.full_alleles[0])
        else:
            record.vcfrecord.INFO['HRUN'] = \
                utils.GetHomopolymerRun(record.ref_allele)
        if np.sum(record.GetCalledSamples()) > 0:
            allele_freqs = record.GetAlleleFreqs(uselength=args.use_length)
            genotype_counts = record.GetGenotypeCounts(uselength=args.use_length)
            record.vcfrecord.INFO['HET'] = utils.GetHeterozygosity(allele_freqs)
            record.vcfrecord.INFO['HWEP'] = utils.GetHardyWeinbergBinomialTest(allele_freqs, genotype_counts)
            allele_counts = record.GetAlleleCounts(index = True)
            n_alleles = len(record.alt_alleles) + 1
            for idx in range(n_alleles):
                if idx not in allele_counts:
                    allele_counts[idx] = 0
            if n_alleles == 1:
                record.vcfrecord.INFO['AC'] = 0
            else:
                record.vcfrecord.INFO['AC'] = \
                    ",".join([str(allele_counts[idx]) for idx in range(1, n_alleles)])
            record.vcfrecord.INFO['REFAC'] = int(allele_counts[0])
        else:
            record.vcfrecord.INFO['HET'] = -1
            record.vcfrecord.INFO['HWEP'] = -1
            if len(record.alt_alleles) == 0:
                record.vcfrecord.INFO['AC'] = 0
            else:
                record.vcfrecord.INFO['AC'] = ','.join(['0']*len(record.alt_alleles))
            record.vcfrecord.INFO['REFAC'] = 0
        # Output the record
        outvcf.write_record(record.vcfrecord)
        # print to stdout if elapsed time is more than 5 sec
        if time.time() - last_notification > 5:
            last_notification = time.time()
            sys.stdout.write('\033[2K\033[1G')
            print("Processed {} loci. Time per locus: {:0.3g}".format(
                record_counter, (time.time() - start)/record_counter), end='\r',
                flush=True)

    invcf.close()
    outvcf.close()

    sys.stdout.write('\033[2K\033[1G')
    print("Done.", flush=True)

    # Output log info
    WriteSampLog(sample_info, invcf.samples, args.out + ".samplog.tab")
    WriteLocLog(loc_info, args.out+".loclog.tab")

    if args.zip:
        proc = sp.run(["tabix", args.out+suffix])
        if proc.returncode != 0:
            common.WARNING("Tabix failed with returncode " +
                           str(proc.returncode))
            return 1

    return 0

def run(): # pragma: no cover
    args = getargs()
    retcode = main(args)
    sys.exit(retcode)

if __name__ == "__main__": # pragma: no cover
    run()
