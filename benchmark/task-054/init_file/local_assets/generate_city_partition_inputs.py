#!/usr/bin/env python3
"""Visible local city inputs and public partition constraints, not assignments."""
from pathlib import Path
import argparse,json,math

TASK='urbanwind-task-054'
FORMULA='(max(cells_per_rank)-mean(cells_per_rank))/mean(cells_per_rank)'
BOXES=[
    [.5,1.5,.5,1.5,1.5],[2.5,3.5,.5,1.5,1.75],[4.5,5.5,.5,1.5,1.],[6.5,7.5,.5,1.5,1.25],
    [1.,2.,2.5,3.5,1.75],[3.,4.,2.5,3.5,1.],[5.,6.,2.5,3.5,1.25],[7.,8.,2.5,3.5,1.5],
    [.5,1.5,4.5,5.5,1.],[2.5,3.5,4.5,5.5,1.25],[4.5,5.5,4.5,5.5,1.5],[6.5,7.5,4.5,5.5,1.75],
    [1.,2.,6.5,7.5,1.25],[3.,4.,6.5,7.5,1.5],[5.,6.,6.5,7.5,1.75],[7.,8.,6.5,7.5,1.]]
PATCHES={'inlet':{'type':'patch','axis':0,'position':-4.,'normal':-1,'area':112.},
         'outlet':{'type':'patch','axis':0,'position':16.,'normal':1,'area':112.},
         'sideLow':{'type':'symmetryPlane','axis':1,'position':-4.,'normal':-1,'area':140.},
         'sideHigh':{'type':'symmetryPlane','axis':1,'position':12.,'normal':1,'area':140.},
         'ground':{'type':'wall','axis':2,'position':0.,'normal':-1,'area':304.},
         'top':{'type':'symmetryPlane','axis':2,'position':7.,'normal':1,'area':320.},
         'building':{'type':'wall','area':104.}}

def contract():
    return {'schema_version':'1.0','task_id':TASK,'case_ids':['task-054-case-001'],
        'methods':['simple','scotch'],'mpi_ranks':4,'flow_solver_runs':0,
        'geometry':{'domain_bounds_m':[[-4.,16.],[-4.,12.],[0.,7.]],'building_boxes_xy_height_m':BOXES,
                    'cell_edge_m':.25,'cell_count':141952,'solid_volume_m3':22.,'fluid_volume_m3':2218.,
                    'coordinate_system':'local_cartesian_m_not_geographic'},'patches':PATCHES,
        'primary_metric':{'id':'cell_load_imbalance_ratio','unit':'fraction','formula':FORMULA,
                          'aggregation':'per_method_mapping_within_one_comparison_case',
                          'value_schema':{'simple':'finite number','scotch':'finite number'}},
        'secondary_metrics':{'communication_face_imbalance':FORMULA.replace('cells_per_rank','communication_faces_per_rank'),
                             'unique_processor_faces':'count unique serial internal faces whose owner and neighbour belong to different ranks'},
        'hard_gates':{'coordinate_tolerance_m':1e-8,'relative_volume_tolerance':1e-8,'area_tolerance_m2':1e-8,
                      'cell_count':141952,'uniform_resolution_m':.25,'raw_mesh_bytes_max':128*1024**2,
                      'all_cells_assigned_exactly_once':True,'all_addressing_and_reciprocal_interfaces':True,
                      'serial_full_checkMesh':'Mesh OK and no Failed checks',
                      'numeric_metric_tolerance':1e-8,'load_imbalance_threshold':None},
        'method_policy':'simple: any positive integer n with product 4 and positive finite delta; scotch: method scotch. Same serial mesh in both methods. Partition identity and hidden metric equality are never required.',
        'original_tool_sequence':['blockMesh','checkMesh','decomposePar'],
        'replay_tool_sequence':['blockMesh','checkMesh','decomposePar','checkMesh.parallel'],
        'replay_policy':'Isolated safe submitted dictionaries; actual pinned v2412 tools and packaged MPI, four ranks. Fresh parallel check is a post-run/pre-agent supplemental gate, not a claim about the September04 logs.',
        'software':'OpenCFD-v2412','decision_policy':'Report BOTH methods; compare load and communication tradeoffs with evidence. No required winner or post-hoc imbalance ceiling.',
        'truth_boundary':'Local synthetic city and genuine OpenFOAM mesh partition tool reference only; no CFD/LES, communication timing, runtime speedup, experimental or real-city validation.',
        'freeze_disclosure':'Primary formula, two methods and four ranks were frozen before September04 tools. Expanded addressing, reporting, safe replay and deployment gates are added after viewing that candidate, before agent evaluation.'}

def header(name):return f'FoamFile{{version 2.0; format ascii; class dictionary; object {name};}}\n'

def geometry():
    return {'type':'FeatureCollection','crs_contract':{'type':'local_cartesian','axis':'x-east,y-north,z-up','unit':'m'},
        'features':[{'type':'Feature','properties':{'id':f'building-{i+1:02d}','height_m':b[4]},
            'geometry':{'type':'Polygon','coordinates':[[[b[0],b[2]],[b[1],b[2]],[b[1],b[3]],[b[0],b[3]],[b[0],b[2]]]]}} for i,b in enumerate(BOXES)]}

def block_mesh():
    axes=[sorted({-4.,16.}|{v for b in BOXES for v in b[:2]}),
          sorted({-4.,12.}|{v for b in BOXES for v in b[2:4]}),sorted({0.,7.}|{b[4] for b in BOXES})]
    counts=[[round((b-a)/.25) for a,b in zip(ax,ax[1:])] for ax in axes];nx,ny,nz=map(len,axes)
    vid=lambda i,j,k:k*nx*ny+j*nx+i
    solid=set()
    for k in range(nz-1):
        for j in range(ny-1):
            for i in range(nx-1):
                x,y,z=[(a[q]+a[q+1])/2 for a,q in zip(axes,(i,j,k))]
                if any(b[0]<x<b[1] and b[2]<y<b[3] and z<b[4] for b in BOXES):solid.add((i,j,k))
    faces={p:[] for p in PATCHES};blocks=[];n=0
    for k in range(nz-1):
        for j in range(ny-1):
            for i in range(nx-1):
                if (i,j,k) in solid:continue
                v=[vid(i,j,k),vid(i+1,j,k),vid(i+1,j+1,k),vid(i,j+1,k),vid(i,j,k+1),vid(i+1,j,k+1),vid(i+1,j+1,k+1),vid(i,j+1,k+1)]
                ns=[a[q] for a,q in zip(counts,(i,j,k))];n+=math.prod(ns)
                blocks.append('hex ('+' '.join(map(str,v))+') ('+' '.join(map(str,ns))+') simpleGrading (1 1 1)')
                fs=[(v[0],v[4],v[7],v[3]),(v[1],v[2],v[6],v[5]),(v[0],v[1],v[5],v[4]),(v[3],v[7],v[6],v[2]),(v[0],v[3],v[2],v[1]),(v[4],v[5],v[6],v[7])]
                for d,(shift,name,f) in enumerate(zip([(-1,0,0),(1,0,0),(0,-1,0),(0,1,0),(0,0,-1),(0,0,1)],list(PATCHES)[:6],fs)):
                    nb=tuple(q+a for q,a in zip((i,j,k),shift));outside=any(q<0 or q>=m-1 for q,m in zip(nb,(nx,ny,nz)))
                    if outside:faces[name].append(f)
                    elif nb in solid:faces['building'].append(f)
    if n!=141952:raise ValueError('visible input cell count changed')
    out=header('blockMeshDict')+'convertToMeters 1;\nvertices (\n'+'\n'.join(f'({x} {y} {z})' for z in axes[2] for y in axes[1] for x in axes[0])+'\n);\nblocks (\n'+'\n'.join(blocks)+'\n);\nedges ();\nboundary (\n'
    for name,fs in faces.items():out+=name+' {type '+PATCHES[name]['type']+'; faces (\n'+'\n'.join('('+' '.join(map(str,f))+')' for f in fs)+'\n);}\n'
    return out+');\nmergePatchPairs ();\n'

def generate(destination):
    dst=Path(destination);(dst/'system').mkdir(parents=True,exist_ok=False)
    (dst/'system/blockMeshDict').write_text(block_mesh(),encoding='utf-8')
    for name,text in {'controlDict':'application checkMesh; startFrom startTime; startTime 0; stopAt endTime; endTime 0; deltaT 1; writeControl timeStep; writeInterval 1; runTimeModifiable false;',
                      'fvSchemes':'ddtSchemes {default steadyState;} gradSchemes {default Gauss linear;} divSchemes {default none;} laplacianSchemes {default Gauss linear corrected;} interpolationSchemes {default linear;} snGradSchemes {default corrected;}',
                      'fvSolution':'solvers {}'}.items():(dst/'system'/name).write_text(header(name)+text+'\n',encoding='utf-8')
    (dst/'partition_contract.json').write_text(json.dumps(contract(),ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    for method in ['simple','scotch']:
        p=dst/'method_configs'/method;p.mkdir(parents=True)
        (p/'decomposeParDict').write_text(header('decomposeParDict')+f'numberOfSubdomains 4; method {method};\n'+('simpleCoeffs {n (2 2 1); delta 0.001;}\n' if method=='simple' else ''),encoding='utf-8')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('destination',type=Path);a=p.parse_args();generate(a.destination)
