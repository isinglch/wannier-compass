"""Task-first review consistency for a selected manifold, not basis approval.

Raw witnesses check traceability only. No energy, weight or shell-completeness
threshold here decides whether a channel is physically needed.
"""

FORMAT = 'task-scope-review-v1'
S_BLOCK = set('H He Li Be Na Mg K Ca Rb Sr Cs Ba Fr Ra'.split())
P_BLOCK = set('B C N O F Ne Al Si P S Cl Ar Ga Ge As Se Br Kr In Sn Sb Te I Xe Tl Pb Bi Po At Rn Nh Fl Mc Lv Ts Og'.split())
D_BLOCK = set('Sc Ti V Cr Mn Fe Co Ni Cu Zn Y Zr Nb Mo Tc Ru Rh Pd Ag Cd Hf Ta W Re Os Ir Pt Au Hg Rf Db Sg Bh Hs Mt Ds Rg Cn'.split())
# An inspection prior for the f series, including its chemically variable ends.
# Actual valence/empty/deep f character must be reviewed in the current data.
F_SERIES = set('La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr'.split())
# Optional empty-d inspection prompts, separate from the main block-shell prior.
OPTIONAL_HINT_SHELLS = {element: ('d',) for element in ('Ca', 'Sr', 'Ba')}
TASK_ROLES = {'task_component', 'exploratory_task_component',
              'secondary_hybridization', 'deep_only', 'no_task_role'}


def chemical_hint(element):
    """Main chemical starting shell; neither automatic inclusion nor full ns+np."""
    return ('s',) if element in S_BLOCK else ('p',) if element in P_BLOCK else ('d',) if element in D_BLOCK else ('f',) if element in F_SERIES else ()


def starting_basis(groups):
    return sorted(name for name in groups
                  if name.split(':')[1] in chemical_hint(name.split(':')[0]))


def text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Task-scope review needs nonempty ' + field + '.')


def ranges(value, nbands, field):
    if not isinstance(value, list) or not value:
        raise ValueError('Task-scope review needs explicit ' + field + '.')
    result = set()
    for pair in value:
        if (not isinstance(pair, list) or len(pair) != 2 or
                any(type(n) is not int for n in pair) or not 1 <= pair[0] <= pair[1] <= nbands):
            raise ValueError('Invalid task-scope band range: ' + field)
        current = set(range(pair[0] - 1, pair[1]))
        if result & current:
            raise ValueError('Overlapping task-scope band ranges: ' + field)
        result.update(current)
    return result


def validate(scope, additional, decisions, e, groups, intent=None, target=None):
    """Validate a declared objective before target/basis/window calculations.

    An explicit task anchor is distinct from rows later expanded to complete the
    selected target. Deep blocks mean actually concentrated deep spectral
    blocks, not the whole occupied manifold by default: task-relevant lower
    valence and additional s channels remain possible. Physics remains a review
    responsibility. Neither a
    nonzero witness nor a successful check proves a channel is necessary.
    """
    expected = {'format', 'objective_kind', 'objective', 'requested_scope_evidence',
                'task_anchor_band_ranges_1based', 'deep_blocks'}
    if not isinstance(scope, dict) or set(scope) != expected or scope.get('format') != FORMAT:
        raise ValueError('New proposals require task_scope_review v1; explicitly migrate the draft without changing its basis.')
    text(scope['objective'], 'objective')
    kind = scope['objective_kind']
    if kind not in ('primary_task_manifold', 'explicitly_requested_extended_manifold'):
        raise ValueError('Declare the task objective_kind before selecting the manifold.')
    extended = kind == 'explicitly_requested_extended_manifold'
    if extended:
        text(scope['requested_scope_evidence'], 'requested_scope_evidence for an explicitly requested extended manifold')
    elif scope['requested_scope_evidence'] is not None:
        raise ValueError('Primary task scope uses requested_scope_evidence=null.')
    anchor = ranges(scope['task_anchor_band_ranges_1based'], e.shape[1], 'task_anchor_band_ranges_1based')
    blocks = scope['deep_blocks']
    if not isinstance(blocks, list):
        raise ValueError('deep_blocks must record reviewed roles, with [] when none are identified.')
    deep_rows = set()
    non_task_rows = set()
    deep_target_rows = set()
    background_groups = set()
    for block in blocks:
        if (not isinstance(block, dict) or
                set(block) != {'band_range_1based', 'orbital_groups', 'role', 'reason'}):
            raise ValueError('Each deep block needs explicit rows, orbital_groups, role and reason.')
        rows = ranges([block['band_range_1based']], e.shape[1], 'deep_blocks.band_range_1based')
        names = block['orbital_groups']
        if (not isinstance(names, list) or not names or len(names) != len(set(names)) or
                not set(names) <= set(groups)):
            raise ValueError('Deep-block orbital_groups must identify available channels once.')
        if block['role'] not in ('task_target', 'retained_background', 'outside_task'):
            raise ValueError('Declare deep-block task_target/retained_background/outside_task role.')
        text(block['reason'], 'deep_blocks.reason')
        if deep_rows & rows:
            raise ValueError('Deep-block row roles must not overlap; resolve their task scope explicitly.')
        deep_rows.update(rows)
        if block['role'] == 'task_target':
            deep_target_rows.update(rows)
        else:
            non_task_rows.update(rows)
            background_groups.update(names)
    if anchor & non_task_rows:
        raise ValueError('Task anchors cannot be retained-background or outside-task deep rows.')
    if target is not None and set(target) & non_task_rows:
        raise ValueError('A deep block declared background/outside_task cannot silently become target rows.')
    if target is not None and not (anchor | deep_target_rows) <= set(target):
        raise ValueError('The final target must retain every task-anchor row and explicitly requested deep task-target row.')
    if not isinstance(decisions, list) or not decisions:
        raise ValueError('Task-scope review needs orbital decisions.')
    names = [row.get('group') for row in decisions if isinstance(row, dict)]
    if len(names) != len(decisions) or len(names) != len(set(names)) or set(names) != set(groups):
        raise ValueError('Review every channel once for its task role and both inclusion/omission risks.')
    included = set()
    roles = {}
    for row in decisions:
        name = row['group']
        role = row.get('task_role')
        if role not in TASK_ROLES:
            raise ValueError('Declare task_role for ' + name)
        for field in ('reason', 'risk_if_included', 'risk_if_omitted'):
            text(row.get(field), name + '.' + field)
        roles[name] = role
        if role == 'deep_only' and name not in background_groups:
            raise ValueError('deep_only needs a reviewed non-task deep block: ' + name)
        if row.get('action') == 'include':
            included.add(name)
            if role in ('deep_only', 'no_task_role'):
                raise ValueError('An included channel cannot have a deep_only/no_task_role justification: ' + name)
            if role == 'exploratory_task_component' and (not isinstance(intent, dict) or intent.get('mode') != 'exploratory'):
                raise ValueError('Unresolved task-component inclusion requires trial_intent.mode=exploratory: ' + name)
    extra = included - set(starting_basis(groups))
    if not isinstance(additional, dict) or set(additional) != extra:
        raise ValueError('additional_basis_evidence must review exactly the included additional channels: ' + str(sorted(extra)))
    witnesses = {}
    for name, item in additional.items():
        if (not isinstance(item, dict) or
                set(item) != {'witness_k_band_1based', 'representation_reason', 'independent_of_deep_block'}):
            raise ValueError('Additional basis evidence needs a task witness, representation reason and deep-block independence: ' + name)
        if roles[name] not in ('task_component', 'exploratory_task_component'):
            raise ValueError('An additional channel needs a positive or exploratory task-component role: ' + name)
        text(item['representation_reason'], name + '.representation_reason')
        coordinate = item['witness_k_band_1based']
        if (not isinstance(coordinate, list) or len(coordinate) != 2 or
                any(type(n) is not int for n in coordinate) or
                not 1 <= coordinate[0] <= e.shape[0] or not 1 <= coordinate[1] <= e.shape[1]):
            raise ValueError('Invalid additional-channel witness coordinate: ' + name)
        k, b = (n - 1 for n in coordinate)
        if b not in anchor:
            raise ValueError('Additional-channel witness must belong to the independent task anchor, not merely expanded target rows: ' + name)
        # Zero cannot witness that channel. Every positive raw value is valid
        # traceability, however small; no strength/energy sufficiency gate.
        if not groups[name][k, b] > 0:
            raise ValueError('Additional-channel witness has zero raw projection; cite an actual channel state: ' + name)
        independent = item['independent_of_deep_block']
        if type(independent) is not bool:
            raise ValueError('independent_of_deep_block must be a reviewed boolean: ' + name)
        if not extended and (not independent or b in deep_rows):
            raise ValueError('Primary-task additional channels need evidence independent of their deep block; adding that block to target is not evidence: ' + name)
        if extended and ((independent and b in deep_rows) or (not independent and b not in deep_target_rows)):
            raise ValueError('Extended deep-block witness must be explicitly requested and declared task_target; keep independence truthful: ' + name)
        witnesses[name] = {'k_band_1based': coordinate, 'energy_eV': float(e[k, b]),
                           'raw_weight': float(groups[name][k, b]),
                           'role': 'coordinate traceability only; not channel sufficiency or basis approval'}
    return {'format': FORMAT, 'objective_kind': kind, 'additional_channel_witnesses': witnesses,
            'role': 'Task-scope consistency only. No global gap is needed to omit a deep-only channel; exclude_bands, dis_min and frozen_min remain independent.'}
