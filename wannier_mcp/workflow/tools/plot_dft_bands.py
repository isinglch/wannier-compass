#!/usr/bin/env python3
"""Plot the current DFT bands and every element:s/p/d/f raw projection.

Display only: no orbital/window selection, DOS calculation or fit acceptance.
All coordinates and energies come from the existing authenticated band cache.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from orbital_evidence import read_cache
from plot_fit_review import interval, verify_path, window_display_ranges


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verified_path(cache, path_file, cache_hash, meta, nk):
    with np.load(cache, allow_pickle=False) as z:
        coords = np.asarray(z['kcoords'], dtype=float)
        if 'path_geometry' not in z:
            raise ValueError('A verified band_path geometry is required for the standard plots.')
        geometry = json.loads(str(z['path_geometry']))
    if meta.get('sampling') != 'band_path' or coords.shape != (1,nk,3) or not np.isfinite(coords).all():
        raise ValueError('Require the existing one-channel band_path cache with its complete finite k sequence.')
    if path_file is None:
        source = meta.get('source_files',{}).get('kpoints',{})
        if not source.get('sha256') or source['sha256'] != geometry.get('source_kpoints_sha256'):
            raise ValueError('Embedded geometry must authenticate the original KPOINTS source.')
        labels = {tick['index_1based']:tick['label'] for tick in geometry.get('ticks',[])}
        segments = []
        for first,last in geometry.get('segments_1based',[]):
            if type(first) is not int or type(last) is not int or not 1 <= first < last <= nk:
                raise ValueError('Invalid embedded path segment.')
            segments.append(dict(start=first-1,stop=last,
                start_fractional=coords[0,first-1].tolist(),end_fractional=coords[0,last-1].tolist(),
                start_label=labels.get(first,''),end_label=labels.get(last,'')))
        path = dict(coordinate_system='fractional_reciprocal',cache_sha256=cache_hash,
            procar_sha256=meta.get('procar_sha256'),source_kpoints_sha256=source['sha256'],
            segments=segments)
    else:
        path = json.loads(Path(path_file).read_text())
    if path.get('cache_sha256') != cache_hash or path.get('procar_sha256') != meta.get('procar_sha256'):
        raise ValueError('fit_path.json does not authenticate this exact cache and PROCAR.')
    if geometry.get('kcoords_type') != 'fractional' or path.get('coordinate_system') != 'fractional_reciprocal':
        raise ValueError('Explicit fractional reciprocal coordinates are required.')
    if not geometry.get('source_kpoints_sha256') or path.get('source_kpoints_sha256') != geometry['source_kpoints_sha256']:
        raise ValueError('DFT path/cache KPOINTS source hashes differ or are missing.')
    reciprocal = np.asarray(geometry.get('reciprocal_lattice_2pi_per_angstrom'), dtype=float)
    if reciprocal.shape != (3,3) or not np.isfinite(reciprocal).all() or abs(np.linalg.det(reciprocal)) < 1e-12:
        raise ValueError('Require three nonsingular reciprocal row vectors, including 2pi, in inverse Angstrom.')
    k, x, stop_previous = coords[0], np.full(nk,np.nan), 0
    segments = path.get('segments',[])
    if not segments:
        raise ValueError('Explicit path segments are required; no inferred connections across gaps.')
    for index, segment in enumerate(segments):
        start, stop = segment['start'], segment['stop']
        if type(start) is not int or type(stop) is not int or not 0 <= start < stop <= nk or stop-start < 2:
            raise ValueError('Each segment must be a valid half-open integer slice with at least two samples.')
        if start not in ([0] if index == 0 else [stop_previous-1,stop_previous]) or stop <= stop_previous:
            raise ValueError('Segments must cover the stored sequence in order, with at most one shared endpoint.')
        steps = np.linalg.norm(np.diff(k[start:stop],axis=0) @ reciprocal,axis=1)
        if not np.isfinite(steps).all() or np.any(steps <= 0):
            raise ValueError('Each segment must have strictly increasing physical distance.')
        origin = 0. if index == 0 else x[stop_previous-1]
        x[start:stop] = origin + np.r_[0.,np.cumsum(steps)]
        stop_previous = stop
    if stop_previous != nk or not np.isfinite(x).all():
        raise ValueError('Path segments must cover all stored samples.')
    if geometry.get('segments_1based') != [[s['start']+1,s['stop']] for s in segments]:
        raise ValueError('Embedded geometry and fit_path disagree about segment slices.')
    verify_path(cache,k,x,path,1e-6)
    return path,k,x,reciprocal


def raw_scale(groups, marker_area):
    """One common color scale; marker area remains raw weight times fixed area."""
    if not np.isfinite(marker_area) or marker_area <= 0:
        raise ValueError('Marker area per unit raw weight must be finite and positive.')
    maximum = max(float(w.max()) for w in groups.values())
    return dict(color_vmin=0.,color_vmax=max(1.,float(np.ceil(maximum*10)/10)),
                marker_area_per_raw_unit_pt2=float(marker_area),observed_global_raw_max=maximum,
                self_normalization=False,per_atom_averaging=False,weight_clipping=False,
                aggregation='sum of original charge projections over every atom of the element and every m component of the complete shell')


def tick_locations(path,x):
    ticks = {}
    for s in path['segments']:
        for index,label in ((s['start'],s.get('start_label','')),(s['stop']-1,s.get('end_label',''))):
            if label:
                bucket = ticks.setdefault(float(x[index]),[])
                if label not in bucket:
                    bucket.append(label)
    return list(ticks),[' | '.join(labels) for labels in ticks.values()]


def run(args):
    out = Path(args.out_dir).resolve()
    if out.exists():
        raise ValueError('Use a new plots directory; existing output directories are never overwritten.')
    meta,e,groups,cache_hash = read_cache(args.cache)
    display=interval(args.energy_range,'Explicit display range') if args.energy_range is not None else None
    focus=interval(args.seed,'Explicit detail range') if args.seed is not None else None
    path,k,x,reciprocal = verified_path(args.cache,args.path,cache_hash,meta,len(e))
    scale = raw_scale(groups,args.marker_area)
    span = float(e.max()-e.min())
    padding = max(.1,span*.015)
    full = [float(e.min()-padding),float(e.max()+padding)]
    views=[('All computed states',full)];ranges=dict(all_computed_eV=full)
    decision_path=getattr(args,'decision',None)
    if decision_path:
        decision=json.loads(Path(decision_path).read_text())
        source=decision.get('evidence',{}).get('source_sha256')
        if source is not None and source!=meta['procar_sha256']:
            raise ValueError('Decision and DFT projection source differ.')
        context=window_display_ranges(decision['windows'])
        ranges.update(context)
        views.extend([('Frozen window with context',context['frozen_context']),('Outer window with context',context['outer_context'])])
    if display is not None:
        views.append(('Explicit display range',display));ranges['requested_display_eV']=display
    if focus is not None:
        views.append(('Explicit detail range',focus));ranges['requested_detail_eV']=focus
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import Normalize
    from matplotlib.cm import ScalarMappable

    out.mkdir(parents=True,exist_ok=False)
    positions,labels = tick_locations(path,x)
    outputs = []
    def decorate(ax,limits):
        ax.axhline(0,color='#52637a',lw=.8,ls=':')
        for position in positions:
            ax.axvline(position,color='.86',lw=.6)
        if positions:
            ax.set_xticks(positions,labels)
            ax.tick_params(axis='x',labelsize=8)
        ax.set(xlim=(x[0],x[-1]),ylim=limits,ylabel=r'$E-E_F$ (eV)')
        ax.tick_params(axis='y',labelsize=8)
    def bands(ax,color='.50',alpha=.8):
        # One plot call per segment: never connect unrelated endpoints.
        for segment in path['segments']:
            sl = slice(segment['start'],segment['stop'])
            ax.plot(x[sl],e[sl],color=color,lw=.55,alpha=alpha,rasterized=True)
    def save(fig,stem):
        for ext in ('png','pdf'):
            file = out/(stem+'.'+ext)
            fig.savefig(file,dpi=170)
            outputs.append(file)
        plt.close(fig)

    fig,axes = plt.subplots(1,len(views),figsize=(max(7.,5.4*len(views)),5.2),constrained_layout=True,squeeze=False)
    axes=axes.ravel()
    for ax,(title,limits) in zip(axes,views):
        bands(ax,color='#243750',alpha=.9)
        decorate(ax,limits)
        ax.set_title(title,fontsize=11)
    fig.suptitle((args.title+' | ' if args.title else '')+'DFT band structure',fontsize=14)
    fig.supxlabel(r'Segmented reciprocal-path distance ($\mathrm{\AA}^{-1}$); jumps excluded',fontsize=10)
    save(fig,'dft_bands')

    norm = Normalize(vmin=scale['color_vmin'],vmax=scale['color_vmax'],clip=False)
    cmap = plt.get_cmap('viridis')
    compact=len(views)==1
    ncols=2 if compact else len(views)
    nrows=(len(groups)+1)//2 if compact else len(groups)
    fig,axes = plt.subplots(nrows,ncols,figsize=(7.*ncols,3.5*nrows),squeeze=False,constrained_layout=True)
    xx = np.broadcast_to(x[:,None],e.shape)
    group_summary = {}
    for row,(group,weight) in enumerate(groups.items()):
        per_view={name:float(weight[(e>=pair[0])&(e<=pair[1])].max()) if ((e>=pair[0])&(e<=pair[1])).any() else None
                  for name,pair in ranges.items()}
        group_summary[group] = dict(global_raw_max=float(weight.max()),raw_max_by_display=per_view,
            all_path_raw_sum=float(weight.sum()),zero_everywhere=bool(not weight.any()))
        for col,(title,limits) in enumerate(views):
            ax = axes[row//2,row%2] if compact else axes[row,col]
            bands(ax,color='.70',alpha=.55)
            visible = (e>=limits[0])&(e<=limits[1])&(weight>0)
            ax.scatter(xx[visible],e[visible],s=args.marker_area*weight[visible],c=weight[visible],
                       cmap=cmap,norm=norm,linewidths=0,alpha=.85,rasterized=True)
            decorate(ax,limits)
            max_text = f"{group_summary[group]['global_raw_max']:.3f}"
            ax.set_title(f'{group} | {title}\nraw max (all sampled states) = {max_text}',fontsize=9)
    if compact and len(groups)%2:
        axes[-1,-1].set_visible(False)
    colorbar = fig.colorbar(ScalarMappable(norm=norm,cmap=cmap),ax=axes.ravel().tolist(),fraction=.013,pad=.01)
    colorbar.set_label('Raw element × complete-shell weight (common scale)',fontsize=10)
    fig.suptitle((args.title+' | ' if args.title else '')+'DFT raw element × shell projections\n'
                 f'Common scale; marker area = {args.marker_area:g} × raw weight pt²',fontsize=12)
    fig.supxlabel(r'Segmented reciprocal-path distance ($\mathrm{\AA}^{-1}$)',fontsize=10)
    save(fig,'projected_bands')
    manifest = dict(format='dft-band-plots-v1',status='dft_visual_review_only',fit_pass=None,
        sources=dict(cache=dict(path=str(Path(args.cache).resolve()),sha256=cache_hash),
                     fit_path=dict(path=str(Path(args.path).resolve()),sha256=sha(args.path)) if args.path else None,
                     path_geometry_source='embedded_verified_band_cache' if not args.path else 'authenticated_fit_path',
                     procar_sha256=meta['procar_sha256'],ef_source=meta['ef_source'],ef_provenance=meta.get('ef_provenance')),
        ef_eV=meta['ef'],energy_reference='original absolute DFT energies minus the cache SCF EF; no fitted shift',
        nkpoints=e.shape[0],nbands=e.shape[1],species=meta['species'],counts=meta['counts'],groups=list(groups),
        limits=ranges,
        decision_display_source=dict(path=str(Path(decision_path).resolve()),sha256=sha(decision_path)) if decision_path else None,
        raw_projection_scale=scale,group_raw_diagnostics=group_summary,
        path=dict(distance_units='inverse_Angstrom',reciprocal_lattice_2pi_per_angstrom=reciprocal.tolist(),
                  segments=path['segments'],distance_inv_angstrom=x.tolist(),
                  k_sequence_retained=True,duplicate_endpoints_retained=True,jumps_excluded=True,
                  cross_segment_lines=False,coordinate_reordering=False),
        limitations=['Display ranges are annotations, not orbital/window-selection criteria.',
            'Projected weights are the original atomic charge projections, not whole-space orbital probabilities.',
            'No self-normalization, DOS calculation, new DFT calculation or fit approval.',
            'Raw sums over sampled states are diagnostics, not BZ-integrated populations.',
            'All element:s/p/d/f groups are shown, including zero-weight channels; no selection is performed.'],
        tool=dict(script_sha256=sha(__file__),cache_adapter_sha256=sha(Path(__file__).with_name('orbital_evidence.py')),
                  path_verifier_sha256=sha(Path(__file__).with_name('plot_fit_review.py')),
                  numpy_version=np.__version__,matplotlib_version=matplotlib.__version__),
        outputs={p.name:dict(path=str(p),sha256=sha(p),bytes=p.stat().st_size) for p in outputs})
    with (out/'manifest.json').open('x') as stream:
        json.dump(manifest,stream,ensure_ascii=False,allow_nan=False,indent=2)
        stream.write('\n')
    return dict(status=manifest['status'],output_dir=str(out),manifest=str(out/'manifest.json'),fit_pass=None)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache',required=True)
    p.add_argument('--path',help='Optional authenticated fit_path.json; otherwise use the verified geometry embedded in the band cache.')
    p.add_argument('--out-dir',required=True,help='New directory, normally material/02_band/plots.')
    p.add_argument('--energy-range',nargs=2,type=float,metavar=('EMIN','EMAX'),help='Optional extra view; default shows all computed states.')
    p.add_argument('--focus-range','--seed',dest='seed',nargs=2,type=float,metavar=('EMIN','EMAX'),help='Optional explicitly chosen detail view; no EF default.')
    p.add_argument('--decision',help='Optional current window record: add frozen +/-1 and outer +/-3 eV context views.')
    p.add_argument('--marker-area',type=float,default=48.,help='Fixed points-squared per unit raw projection.')
    p.add_argument('--title',default='')
    args = p.parse_args()
    try:
        print(json.dumps(run(args),allow_nan=False))
    except (ValueError,KeyError,OSError) as exc:
        p.exit(2,str(exc)+'\n')


if __name__ == '__main__':
    main()
