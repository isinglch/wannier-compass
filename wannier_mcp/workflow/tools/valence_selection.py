"""Reviewed occupied-prefix exclusion; original DFT IDs remain authoritative.

A filled shell or a direct gap alone is not a removal criterion. A reviewed
spectral block need not exhaust an atomic shell. Conservative prefix removal
uses sampled global clearance, occupations and physical review. No material lookup.
"""
import re
import numpy as np
from orbital_channels import basis_dimension


def occupations(cache):
    with np.load(cache, allow_pickle=False) as z:
        if 'occupations' not in z:
            return None
        o = np.asarray(z['occupations'], dtype=float)
    return o[0] if o.ndim == 3 and o.shape[0] == 1 else o


def parse_exclude(value):
    ids = []
    for token in re.split(r'[,;\s]+', value.strip()):
        if not token:
            continue
        match = re.fullmatch(r'(\d+)(?:[-:](\d+))?', token)
        if not match:
            raise ValueError('Invalid exclude_bands syntax.')
        lo, hi = int(match[1]), int(match[2] or match[1])
        if not 1 <= lo <= hi:
            raise ValueError('Invalid exclude_bands range.')
        ids.extend(range(lo, hi+1))
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate exclude_bands index.')
    return sorted(ids)


def retained_indices(selection, nbands):
    if selection is None:
        return np.arange(nbands)
    ids = selection['exclude_bands_1based']
    if (selection['original_nbands'] != nbands or
            any(type(i) is not int for i in ids) or
            ids != list(range(1, len(ids)+1)) or len(ids) >= nbands):
        raise ValueError('Exclusion must be a k-independent occupied prefix of original DFT bands.')
    retained = list(range(len(ids)+1, nbands+1))
    if (selection['retained_original_bands_1based'] != retained or
            selection['num_bands_retained'] != len(retained)):
        raise ValueError('Original/retained band mapping mismatch.')
    return np.array(retained, dtype=int)-1


def boundary_evidence(e, cut):
    """Numerical separation only, not orbital identity or full-BZ certification."""
    if e.ndim != 2 or not np.isfinite(e).all() or not 0 < cut < e.shape[1]:
        raise ValueError('Boundary needs finite 2D energies and an internal original-band cut.')
    lower = e[:, cut-1]; upper = e[:, cut]
    direct = upper-lower
    gap = float(upper.min()-lower.max())
    direct_min = float(direct.min())
    kind = ('sampled_global_gap' if gap > 0 else
            'sampled_direct_gap_only' if direct_min > 0 else 'sampled_touching_or_crossing')
    return dict(after_band_1based=cut, gap_above_global_eV=gap,
        minimum_direct_gap_eV=direct_min, lower_max_eV=float(lower.max()),
        upper_min_eV=float(upper.min()), lower_max_k_index_1based=int(lower.argmax())+1,
        upper_min_k_index_1based=int(upper.argmin())+1,
        minimum_direct_gap_k_index_1based=int(direct.argmin())+1,
        sampled_separation=kind)


def prefix_candidates(e, occ, spin_mode, minimum_gap, occupation_tolerance, before_band):
    """List numerically safe shorter prefixes for review; never approve/remove one."""
    if type(before_band) is not int or not 1 <= before_band <= e.shape[1]+1:
        raise ValueError('before_band must be a one-based boundary limit.')
    matching = occ is not None and occ.shape == e.shape and np.isfinite(occ).all()
    full = 1. if spin_mode == 'spinor' else 2.
    rows = []
    for cut in range(min(before_band-1, e.shape[1]-1), 0, -1):
        row = boundary_evidence(e, cut)
        filled = bool(np.all(np.abs(occ[:, :cut]-full) <= occupation_tolerance)) if matching else False
        if filled and row['gap_above_global_eV'] >= minimum_gap:
            rows.append(dict(**row, fully_occupied=True, requires_projection_review=True))
    return dict(before_band_1based=before_band, minimum_gap_eV=minimum_gap,
        occupations_available=bool(matching), candidates_largest_prefix_first=rows,
        no_exclusion_fallback=True, automatically_selected=False,
        scope='Supplied k samples only. Review every removed state and intersect evidence from available path/interface data; a numerical gap does not prove non-target character.')


def sampled_gap_checks(e, occ, selection, spin_mode):
    """Recheck on either path or the actual interface's full DFT EIGENVAL."""
    retained_indices(selection, e.shape[1])
    if not selection or not selection['exclude_bands_1based']:
        return dict(gate_pass=True, blocks=[], scope='No excluded bands.')
    if occ is None or occ.shape != e.shape or not np.isfinite(occ).all():
        raise ValueError('Occupied-block exclusion requires finite matching occupations.')
    full = 1. if spin_mode == 'spinor' else 2.
    rows = []
    for block in selection['reviewed_blocks']:
        lo, hi = block['band_range_1based']
        top = float(e[:, lo-1:hi].max()); next_bottom = float(e[:, hi].min())
        gap = next_bottom-top
        boundary = boundary_evidence(e, hi)
        filled = bool(np.all(np.abs(occ[:,lo-1:hi]-full) <= selection['occupation_tolerance']))
        rows.append(dict(band_range_1based=[lo,hi], gap_above_eV=gap,
            excluded_top_eV=top, retained_next_bottom_eV=next_bottom,
            minimum_direct_gap_eV=boundary['minimum_direct_gap_eV'],
            sampled_separation=boundary['sampled_separation'],
            fully_occupied=filled, gate_pass=filled and gap >= selection['minimum_gap_eV']))
    return dict(gate_pass=all(r['gate_pass'] for r in rows), blocks=rows,
        scope='Global energy separation on supplied k samples only; not an off-grid BZ guarantee.')


def build_selection(e, occ, meta, groups, basis, blocks, minimum_gap, occupation_tolerance):
    if not isinstance(blocks, list):
        raise ValueError('valence_exclusions must be a reviewed list of low-energy spectral blocks.')
    end = 0; seen = set(); complete_shells = set()
    for b in blocks:
        required = {'band_range_1based','orbital_groups','reason'}
        if not required <= set(b) or set(b)-required-{'membership_kind'} or not b['reason'].strip():
            raise ValueError('Each excluded block needs its original band range, shell groups and reason.')
        kind = b.get('membership_kind', 'complete_shells')
        if kind not in ('complete_shells', 'reviewed_spectral_block'):
            raise ValueError('Unknown excluded block membership_kind.')
        lo, hi = b['band_range_1based']
        shells = b['orbital_groups']
        if (type(lo) is not int or type(hi) is not int or lo != end+1 or not lo <= hi < e.shape[1]):
            raise ValueError('Excluded blocks must form a contiguous original-band prefix.')
        if (not shells or len(set(shells)) != len(shells) or set(shells)-set(groups) or
                set(shells)&set(basis) or set(shells)&complete_shells or
                (kind == 'complete_shells' and set(shells)&seen)):
            raise ValueError('Excluded groups must be known, outside the target basis and cannot reuse a complete shell.')
        capacity = basis_dimension(shells,meta['species'],meta['counts'],meta['spin_mode'])
        if kind == 'complete_shells' and hi-lo+1 != capacity:
            raise ValueError('Excluded block band count does not match the reviewed complete shell capacity.')
        end = hi; seen.update(shells)
        if kind == 'complete_shells': complete_shells.update(shells)
    result = dict(format='occupied-prefix-selection-v1', original_nbands=e.shape[1],
        exclude_bands_1based=list(range(1,end+1)), retained_original_bands_1based=list(range(end+1,e.shape[1]+1)),
        num_bands_retained=e.shape[1]-end, reviewed_blocks=blocks,
        minimum_gap_eV=minimum_gap, occupation_tolerance=occupation_tolerance)
    check = sampled_gap_checks(e,occ,result,meta['spin_mode'])
    if not check['gate_pass']:
        raise ValueError('Excluded blocks lack the required sampled global gap or full occupation: '+str(check))
    return result, check
