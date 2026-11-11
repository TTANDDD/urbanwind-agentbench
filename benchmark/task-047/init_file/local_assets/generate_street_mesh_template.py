#!/usr/bin/env python3
"""Public local street/roof/intersection mesh design; no executed mesh or answer."""
from pathlib import Path
import argparse,json,math
from itertools import product

CENTRES=[[.75+2*i+.5*(r%2),1+2*r] for r in range(4) for i in range(4)]
CUBES=[[[x-.5,x+.5],[y-.5,y+.5],[0.,1.]] for x,y in CENTRES]
CANYONS=[[[.25,7.75],[y,y+1],[0.,1.]] for y in [1.5,3.5,5.5]]
ROOFS=[[[.25,7.75],[.5,7.5],[1.,1.5]]]
INTERSECTIONS=[[[x,x+.5],[y-.25,y+1.25],[0.,1.]] for x in [1.75,3.75,5.75] for y in [1.5,3.5,5.5]]
REGIONS=CANYONS+ROOFS+INTERSECTIONS
CUTS=[sorted({0.,8.,*[q for b in CUBES+REGIONS for q in b[d]]}) if d<2 else [0.,1.,1.5,7.4] for d in range(3)]
COARSE=[[1]*(len(x)-1) for x in CUTS]
FORMULA='sum_i volume(raw_axis_aligned_cell_i intersect union(public canyon, roof and intersection boxes)) * I(dx_i,dy_i,dz_i <= stage directional limits) / 51.0 m3; the union is counted once; raw topology must tile the complete 457.6 m3 fluid domain exactly once with all 16 solids excluded; no centre-count approximation'

def union_volume(boxes):
    cuts=[sorted({q for b in boxes for q in b[d]}) for d in range(3)]
    total=0.
    for i,j,k in product(*(range(len(x)-1) for x in cuts)):
        ids=(i,j,k); lo=[cuts[d][ids[d]] for d in range(3)]; hi=[cuts[d][ids[d]+1] for d in range(3)]
        mid=[(a+b)/2 for a,b in zip(lo,hi)]
        if any(all(b[d][0]<mid[d]<b[d][1] for d in range(3)) for b in boxes):total+=math.prod(b-a for a,b in zip(lo,hi))
    return total

def counts_for(stage='full'):
    step=contract()[stage]['roi_resolution_max_m']; upper=contract()[stage]['upper_z_target_m']
    return [[math.ceil((b-a)/(upper if d==2 and a>=1.5 else step[d])-1e-10) for a,b in zip(x,x[1:])] for d,x in enumerate(CUTS)]

def expected_cells(counts):
    total=0
    for ids in product(*(range(len(x)-1) for x in CUTS)):
        mid=[(CUTS[d][q]+CUTS[d][q+1])/2 for d,q in enumerate(ids)]
        if not any(all(b[d][0]<mid[d]<b[d][1] for d in range(3)) for b in CUBES): total+=math.prod(counts[d][q] for d,q in enumerate(ids))
    return total

def contract():
    return {'schema_version':'1.0','task_id':'urbanwind-task-047','case_id':'task-047-case-001',
        'metric':{'id':'street_canyon_resolution_coverage','name_zh':'街谷目标分辨率覆盖率','unit':'fraction','formula':FORMULA},
        'geometry':{'domain_bounds_m':[[0,8],[0,8],[0,7.4]],'cube_bounds_m':CUBES,'centres_xy_m':CENTRES,
                    'cube_height_m':1.,'cube_count':16,'row_offset_m':.5,'building_area_m2':80.,'ground_area_m2':48.,'fluid_volume_m3':457.6},
        'roi':{'canyon_boxes_m':CANYONS,'roof_boxes_m':ROOFS,'intersection_boxes_m':INTERSECTIONS,'union_volume_m3':51.,
               'interpretation':'Three inter-row canyon corridors, a near-roof layer and nine connecting intersection boxes; overlaps are counted exactly once. All boxes are fluid-only.'},
        'qualification':{'roi_resolution_max_m':[.5,.5,.25],'upper_z_target_m':.6,'outside_coarse_dz_min_m':.5},
        'full':{'roi_resolution_max_m':[.25,.25,.125],'upper_z_target_m':.5,'outside_coarse_dz_min_m':.45},
        'gates':{'cells_min':1000,'cells_max':150000,'atomic_grid_voxels_max':2000000,'coordinate_error_m':1e-8,
                 'area_relative_error_max':1e-8,'normal_error_max':1e-8,'raw_box_volume_relative_error_max':1e-9,'volume_relative_error_max':1e-8,
                 'roi_coverage_min':.99999999,'outside_coarse_volume_fraction_min':.8,'aspect_ratio_max':10.,'max_nonorthogonality_deg':1.,'max_skewness':1.,'archive_budget_bytes':300000000},
        'patches':{'xLow':{'type':'patch','axis':0,'position_m':0.,'normal_sign':-1,'area_m2':59.2},
                   'xHigh':{'type':'patch','axis':0,'position_m':8.,'normal_sign':1,'area_m2':59.2},
                   'yLow':{'type':'symmetryPlane','axis':1,'position_m':0.,'normal_sign':-1,'area_m2':59.2},
                   'yHigh':{'type':'symmetryPlane','axis':1,'position_m':8.,'normal_sign':1,'area_m2':59.2},
                   'ground':{'type':'wall','axis':2,'position_m':0.,'normal_sign':-1,'area_m2':48.},
                   'top':{'type':'symmetryPlane','axis':2,'position_m':7.4,'normal_sign':1,'area_m2':64.},'building':{'type':'wall','area_m2':80.}},
        'topology':'Conformal axis-aligned six-quadrilateral-face hexahedra; entire raw domain partition must be exact. Nonperiodic mesh only; no flow fields are supplied or solved.',
        'measurement':'Reconstruct every cell from raw points/faces/owner/neighbour. Split public overlapping ROIs into disjoint boxes before summing exact intersections. Outside coarsening is the volume outside the ROI with dz >= the stage minimum divided by 457.6-51.0 m3, not cell counts.',
        'tolerance':'Round coordinates to 1e-10 m only for the atomic partition proof. All intersections, areas and scales use raw coordinates. Directional comparisons allow 1e-9 relative roundoff.',
        'software':'OpenCFD-v2412','mpi_ranks':16,'physical_cpus':'80-95','flow_solver_runs':0,
        'tool_sequence':['blockMesh','checkMesh','decomposePar','checkMesh.parallel'],
        'legal_alternatives':'Any legal integer counts or conformal axis-aligned layout meeting all public constraints; no GT count, cell ID, connectivity numbering or byte equality is required.',
        'truth_boundary':'Local synthetic array mesh-tool capability only; not periodic coupling, CFD/LES, yplus, wind accuracy, experimental truth or an accepted retry of task046.'}

def header(name):return f'FoamFile{{version 2.0; format ascii; class dictionary; object {name};}}\n'

def geojson():
    return {'type':'FeatureCollection','name':'local-16-equal-staggered-cubes','coordinate_system':'local Cartesian metres; not latitude/longitude',
        'features':[{'type':'Feature','properties':{'id':f'cube-{n+1:02d}','height_m':1.,'source':'deterministic_local_geometry'},
            'geometry':{'type':'Polygon','coordinates':[[[b[0][0],b[1][0]],[b[0][1],b[1][0]],[b[0][1],b[1][1]],[b[0][0],b[1][1]],[b[0][0],b[1][0]]]]}} for n,b in enumerate(CUBES)]}

def generate(destination,counts=None):
    destination=Path(destination); counts=COARSE if counts is None else counts
    if len(counts)!=3 or any(len(c)!=len(x)-1 or any(isinstance(n,bool) or not isinstance(n,int) or n<1 for n in c) for c,x in zip(counts,CUTS)):raise ValueError('three positive integer arrays required')
    if expected_cells(counts)>contract()['gates']['cells_max']:raise ValueError('cell budget')
    nx,ny,nz=map(len,CUTS); index=lambda i,j,k:k*nx*ny+j*nx+i
    verts=[(x,y,z) for z in CUTS[2] for y in CUTS[1] for x in CUTS[0]]; solid=set()
    for ids in product(range(nx-1),range(ny-1),range(nz-1)):
        mid=[(CUTS[d][q]+CUTS[d][q+1])/2 for d,q in enumerate(ids)]
        if any(all(b[d][0]<mid[d]<b[d][1] for d in range(3)) for b in CUBES):solid.add(ids)
    boundary={n:[] for n in contract()['patches']}; blocks=[]
    for k in range(nz-1):
        for j in range(ny-1):
            for i in range(nx-1):
                if (i,j,k) in solid:continue
                v=[index(i,j,k),index(i+1,j,k),index(i+1,j+1,k),index(i,j+1,k),index(i,j,k+1),index(i+1,j,k+1),index(i+1,j+1,k+1),index(i,j+1,k+1)]
                blocks.append('hex ('+' '.join(map(str,v))+') ('+' '.join(str(counts[d][q]) for d,q in enumerate((i,j,k)))+') simpleGrading (1 1 1)')
                faces=[(v[0],v[4],v[7],v[3]),(v[1],v[2],v[6],v[5]),(v[0],v[1],v[5],v[4]),(v[3],v[7],v[6],v[2]),(v[0],v[3],v[2],v[1]),(v[4],v[5],v[6],v[7])]
                for n,edge,f in [('xLow',i==0,faces[0]),('xHigh',i==nx-2,faces[1]),('yLow',j==0,faces[2]),('yHigh',j==ny-2,faces[3]),('ground',k==0,faces[4]),('top',k==nz-2,faces[5])]:
                    if edge:boundary[n].append(f)
                for nb,f in [((i-1,j,k),faces[0]),((i+1,j,k),faces[1]),((i,j-1,k),faces[2]),((i,j+1,k),faces[3]),((i,j,k-1),faces[4]),((i,j,k+1),faces[5])]:
                    if nb in solid:boundary['building'].append(f)
    out=header('blockMeshDict')+'convertToMeters 1;\nvertices (\n'+'\n'.join('('+' '.join(f'{p:g}' for p in v)+')' for v in verts)+'\n);\nblocks (\n'+'\n'.join(blocks)+'\n);\nedges ();\nboundary (\n'
    for name,fs in boundary.items():out+=name+' { type '+contract()['patches'][name]['type']+'; faces ('+' '.join('('+' '.join(map(str,f))+')' for f in fs)+'); }\n'
    out+=');\nmergePatchPairs ();\n'
    files={'system/blockMeshDict':out,'system/controlDict':header('controlDict')+'application blockMesh; startFrom startTime; startTime 0; stopAt endTime; endTime 1; deltaT 1; writeControl timeStep; writeInterval 1; writeFormat ascii; writePrecision 12; writeCompression off; runTimeModifiable false;\n',
        'system/fvSchemes':header('fvSchemes')+'ddtSchemes {default Euler;} gradSchemes {default Gauss linear;} divSchemes {default none;} laplacianSchemes {default Gauss linear corrected;} interpolationSchemes {default linear;} snGradSchemes {default corrected;}\n',
        'system/fvSolution':header('fvSolution')+'solvers {}\n','system/decomposeParDict':header('decomposeParDict')+'numberOfSubdomains 16; method scotch;\n'}
    for rel,value in files.items():
        p=destination/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value,encoding='utf-8',newline='\n')
    (destination/'mesh_counts.json').write_text(json.dumps(counts)+'\n',encoding='utf-8')
    (destination/'street_mesh_contract.json').write_text(json.dumps(contract(),ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('destination',type=Path);p.add_argument('--counts',type=Path);a=p.parse_args()
    if a.destination.exists():raise FileExistsError('fresh output required')
    generate(a.destination,json.loads(a.counts.read_text(encoding='utf-8')) if a.counts else None)
