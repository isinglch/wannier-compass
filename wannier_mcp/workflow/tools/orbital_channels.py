"""Resolved charge-column layouts and whole-shell dimensions.

These are format definitions, not orbital inclusion or window-selection rules.
Keep unavailable shells absent; never manufacture zero f weights for spd data.
"""

SHELL_COMPONENTS = {
    's': ('s',),
    'p': ('py', 'pz', 'px'),
    'd': ('dxy', 'dyz', 'dz2', 'dxz', 'x2-y2'),
    'f': ('fy3x2', 'fxyz', 'fyz2', 'fz3', 'fxz2', 'fzx2', 'fx3'),
}
SHELL_DIMENSIONS = {shell: len(labels) for shell, labels in SHELL_COMPONENTS.items()}
SPD_COLUMNS = tuple(label for shell in ('s', 'p', 'd') for label in SHELL_COMPONENTS[shell])
SPDF_COLUMNS = tuple(label for labels in SHELL_COMPONENTS.values() for label in labels)


def resolved_shells(columns):
    """Validate a complete supported layout; cache column order may vary."""
    if (not isinstance(columns, (list, tuple)) or
            any(not isinstance(v, str) for v in columns) or
            len(set(columns)) != len(columns)):
        raise ValueError('Resolved charge columns must be unique string labels.')
    labels = set(columns)
    if labels == set(SPD_COLUMNS):
        return {shell: SHELL_COMPONENTS[shell] for shell in ('s', 'p', 'd')}
    if labels == set(SPDF_COLUMNS):
        return dict(SHELL_COMPONENTS)
    raise ValueError('Require complete real resolved s/p/d (9) or s/p/d/f (16) columns, '
                     'without tot; coarse, partial and unknown layouts are unsupported.')


def procar_columns(labels):
    """Normalize known d aliases and require the printed VASP component order."""
    columns = [('x2-y2' if v in ('dx2', 'dx2-y2') else v) for v in labels]
    resolved_shells(columns)
    if tuple(columns) not in (SPD_COLUMNS, SPDF_COLUMNS):
        raise ValueError('PROCAR columns must follow a supported ordered resolved charge layout.')
    return columns


def basis_dimension(basis, species, counts, spin_mode):
    """Count full shells at all matching ions; spinor projections use both spins."""
    if spin_mode not in ('scalar', 'spinor'):
        raise ValueError('Whole-shell dimension requires scalar or spinor mode.')
    populations = {s: sum(n for a, n in zip(species, counts) if a == s) for s in species}
    factor = 2 if spin_mode == 'spinor' else 1
    return factor * sum(populations[name.split(':')[0]] * SHELL_DIMENSIONS[name.split(':')[1]]
                        for name in basis)
