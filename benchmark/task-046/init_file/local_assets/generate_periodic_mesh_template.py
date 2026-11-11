#!/usr/bin/env python3
"""Visible, local equal-height staggered-array input; no executed mesh or answer."""
from pathlib import Path
import argparse, json, math

FORMULA = 'sum over both x and y cyclic patch pairs of faces on either side without exactly one translated four-vertex match within 1e-8 m, matching area within 1e-8 relative and opposite unit normal within 1e-8; reciprocal cyclic declarations, complete planes, all 16 solids and mesh quality are independent mandatory gates'
CENTRES = [[.75 + 2*i + .5*(r%2), 1+2*r] for r in range(4) for i in range(4)]
CUBES = [[[x-.5,x+.5],[y-.5,y+.5],[0,1]] for x,y in CENTRES]
CUTS = [sorted({0.,8.,*[q for b in CUBES for q in b[0]]}),
        sorted({0.,8.,*[q for b in CUBES for q in b[1]]}), [0.,1.,7.4]]
COARSE = [[1]*(len(x)-1) for x in CUTS]

def counts_for(step, upper_z_step):
    return [[math.ceil((b-a)/((upper_z_step if d==2 and a>=1 else step))-1e-10) for a,b in zip(x,x[1:])] for d,x in enumerate(CUTS)]

def contract():
    return {'schema_version':'1.0','task_id':'urbanwind-task-046','case_id':'task-046-case-001',
        'metric':{'id':'periodic_face_mismatch_count','name_zh':'周期面对不匹配数量','unit':'faces','formula':FORMULA},
        'geometry':{'domain_bounds_m':[[0,8],[0,8],[0,7.4]],'cube_bounds_m':CUBES,'centres_xy_m':CENTRES,
                    'cube_height_m':1.,'cube_count':16,'row_offset_m':.5,'building_area_m2':80.,
                    'ground_area_m2':48.,'fluid_volume_m3':457.6},
        'periodic':[{'low':'xLow','high':'xHigh','axis':0,'translation_m':[8.,0,0]},
                    {'low':'yLow','high':'yHigh','axis':1,'translation_m':[0,8.,0]}],
        'qualification':{'resolution_max_m':[.5,.5,.4]},'full':{'resolution_max_m':[.25,.25,.25]},
        'gates':{'cells_min':1000,'cells_max':200000,'atomic_grid_voxels_max':2000000,
                 'coordinate_error_m':1e-8,'area_relative_error_max':1e-8,'normal_error_max':1e-8,
                 'raw_box_volume_relative_error_max':1e-9,'volume_relative_error_max':1e-8,
                 'aspect_ratio_max':10.,'max_nonorthogonality_deg':1.,'max_skewness':1.},
        'topology':'Conformal axis-aligned six-quadrilateral-face hexahedra; raw cell boxes must tile the entire fluid domain exactly once and exclude each of the 16 visible solids.',
        'measurement':'Use all raw face vertices, oriented area vectors and owner/neighbour topology. Pair in both directions with a translated vertex bijection, not only a face centre. Missing planes, wrong neighbours, duplicate pairing, wrong geometry or quality are hard rejection regardless of mismatch metric.',
        'tolerance':'Atomic plane indexing rounds coordinates to 1e-10m only for partition proof. All geometric distances/areas and periodic pairing use raw coordinates. Resolution comparisons allow relative 1e-9.',
        'software':'OpenCFD-v2412','mpi_ranks':16,'physical_cpus':'80-95','flow_solver_runs':0,
        'tool_sequence':['blockMesh','checkMesh','decomposePar','checkMesh.parallel'],
        'legal_alternatives':'Other integer resolutions and conformal block layouts satisfying all public constraints are valid; no reference cell count, ID or byte equality is required.',
        'truth_boundary':'Local periodic array mesh-tool reference only; not a periodic precursor flow, CFD/LES prediction, yplus claim or experimental truth.'}

def header(name): return f'FoamFile{{version 2.0; format ascii; class dictionary; object {name};}}\n'

def geojson():
    return {'type':'FeatureCollection','name':'visible-local-staggered-16-identical-cubes',
            'coordinate_system':'local Cartesian metres; not geographic longitude/latitude',
            'features':[{'type':'Feature','properties':{'id':f'cube-{n+1:02d}','height_m':1.,'source':'deterministic_local_geometry'},
                'geometry':{'type':'Polygon','coordinates':[[[b[0][0],b[1][0]],[b[0][1],b[1][0]],[b[0][1],b[1][1]],[b[0][0],b[1][1]],[b[0][0],b[1][0]]]]}} for n,b in enumerate(CUBES)]}

def generate(destination, counts=None):
    destination=Path(destination); counts=COARSE if counts is None else counts
    if len(counts)!=3 or any(len(c)!=len(x)-1 or any(isinstance(n,bool) or not isinstance(n,int) or n<1 for n in c) for c,x in zip(counts,CUTS)): raise ValueError('three positive integer count arrays required')
    nx,ny,nz=map(len,CUTS); index=lambda i,j,k:k*nx*ny+j*nx+i
    verts=[(x,y,z) for z in CUTS[2] for y in CUTS[1] for x in CUTS[0]]
    solid=set()
    for k in range(nz-1):
        for j in range(ny-1):
            for i in range(nx-1):
                mid=[(CUTS[d][q]+CUTS[d][q+1])/2 for d,q in enumerate((i,j,k))]
                if any(all(b[d][0]<mid[d]<b[d][1] for d in range(3)) for b in CUBES): solid.add((i,j,k))
    boundary={n:[] for n in ['xLow','xHigh','yLow','yHigh','ground','top','building']}; blocks=[]
    for k in range(nz-1):
        for j in range(ny-1):
            for i in range(nx-1):
                if (i,j,k) in solid: continue
                v=[index(i,j,k),index(i+1,j,k),index(i+1,j+1,k),index(i,j+1,k),index(i,j,k+1),index(i+1,j,k+1),index(i+1,j+1,k+1),index(i,j+1,k+1)]
                blocks.append('hex ('+' '.join(map(str,v))+') ('+' '.join(str(counts[d][q]) for d,q in enumerate((i,j,k)))+') simpleGrading (1 1 1)')
                faces=[(v[0],v[4],v[7],v[3]),(v[1],v[2],v[6],v[5]),(v[0],v[1],v[5],v[4]),(v[3],v[7],v[6],v[2]),(v[0],v[3],v[2],v[1]),(v[4],v[5],v[6],v[7])]
                for n,edge,f in [('xLow',i==0,faces[0]),('xHigh',i==nx-2,faces[1]),('yLow',j==0,faces[2]),('yHigh',j==ny-2,faces[3]),('ground',k==0,faces[4]),('top',k==nz-2,faces[5])]:
                    if edge: boundary[n].append(f)
                for nb,f in [((i-1,j,k),faces[0]),((i+1,j,k),faces[1]),((i,j-1,k),faces[2]),((i,j+1,k),faces[3]),((i,j,k-1),faces[4]),((i,j,k+1),faces[5])]:
                    if nb in solid: boundary['building'].append(f)
    out=header('blockMeshDict')+'convertToMeters 1;\nvertices (\n'+'\n'.join('('+' '.join(f'{p:g}' for p in v)+')' for v in verts)+'\n);\nblocks (\n'+'\n'.join(blocks)+'\n);\nedges ();\nboundary (\n'
    for name,fs in boundary.items():
        kind='wall' if name in ['building','ground'] else 'symmetryPlane' if name=='top' else 'cyclic'
        extra=''
        for pair in contract()['periodic']:
            if name in [pair['low'],pair['high']]:
                other=pair['high'] if name==pair['low'] else pair['low']; sign=-1 if name==pair['low'] else 1
                # OpenCFD separationVector maps neighbour coordinates to this patch.
                extra=f'neighbourPatch {other}; transform translational; separationVector ('+' '.join(f'{sign*t:g}' for t in pair['translation_m'])+'); '
        out+=name+' { type '+kind+'; '+extra+'faces ('+' '.join('('+' '.join(map(str,f))+')' for f in fs)+'); }\n'
    out+=');\nmergePatchPairs ();\n'
    files={'system/blockMeshDict':out,'system/controlDict':header('controlDict')+'application blockMesh; startFrom startTime; startTime 0; stopAt endTime; endTime 1; deltaT 1; writeControl timeStep; writeInterval 1; writeFormat ascii; writePrecision 12; writeCompression off; runTimeModifiable false;\n',
        'system/fvSchemes':header('fvSchemes')+'ddtSchemes {default Euler;} gradSchemes {default Gauss linear;} divSchemes {default none;} laplacianSchemes {default Gauss linear corrected;} interpolationSchemes {default linear;} snGradSchemes {default corrected;}\n',
        'system/fvSolution':header('fvSolution')+'solvers {}\n','system/decomposeParDict':header('decomposeParDict')+'numberOfSubdomains 16; method scotch;\n'}
    for rel,value in files.items():
        p=destination/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(value,encoding='utf-8',newline='\n')
    (destination/'mesh_counts.json').write_text(json.dumps(counts)+'\n',encoding='utf-8')
    (destination/'periodic_mesh_contract.json').write_text(json.dumps(contract(),ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('destination',type=Path); p.add_argument('--counts',type=Path); a=p.parse_args()
    if a.destination.exists(): raise FileExistsError('fresh output required')
    generate(a.destination,json.loads(a.counts.read_text(encoding='utf-8')) if a.counts else None)
