#!/usr/bin/env python3
"""Public answer-free three-level mesh design generator and constraint contract."""
from pathlib import Path
import argparse,json

CUTS=[[-4,-.5,.5,23],[-6.2,-.5,.5,6.2],[0,4,9]]
TEMPLATE=[[2,2,14],[4,2,4],[4,4]]
FORMULA='h_j=(3009.2/N_j)^(1/3), r_cm=h_coarse/h_medium, r_mf=h_medium/h_fine; refinement_ratio_consistency=1-abs(r_cm-r_mf)/max(r_cm,r_mf), with N_j counted from independently validated raw polyMesh; identical grids and nonuniform directional refinement fail separate hard gates'
SAMPLES=[[-2,.15,1.15],[0,1.5,1.15],[2,.15,1.15],[8,.15,1.15],[1,.15,4.5]]

def contract():
    return {'schema_version':'1.0','task_id':'urbanwind-task-045','metric':{'id':'refinement_ratio_consistency','name_zh':'三层网格加密比一致性','unit':'fraction','formula':FORMULA},
        'case_ids':['task-045-case-001','task-045-case-002','task-045-case-003'],
        'geometry':{'domain_bounds_m':[[-4,23],[-6.2,6.2],[0,9]],'building_bounds_m':[[-.5,.5],[-.5,.5],[0,4]],'fluid_volume_m3':3009.2,
            'building_exposed_areas_m2':{'x_low':4,'x_high':4,'y_low':4,'y_high':4,'roof':1},
            'outer_patch_areas_m2':{'inlet':111.6,'outlet':111.6,'sideLow':243,'sideHigh':243,'ground':333.8,'top':334.8},'macro_cuts_m':CUTS},
        'sampling_points_m':SAMPLES,'sampling_scope':'Common geometric fluid locations, not CFD measurements. No required cell IDs.',
        'stages':{'qualification':{'resolution_max_m':[2,2,2],'cells_min':1000,'cells_max':20000},
            'coarse':{'resolution_max_m':[1,1,1],'cells_min':1000,'cells_max':20000},
            'medium':{'resolution_max_m':[2/3]*3,'cells_min':5000,'cells_max':80000},
            'fine':{'resolution_max_m':[4/9]*3,'cells_min':20000,'cells_max':200000}},
        'gates':{'consistency_min':.99,'refinement_ratio_min':1.3,'refinement_ratio_max':2.0,'directional_ratio_relative_error_max':.01,
            'total_study_cells_max':300000,'atomic_grid_voxels_max':2000000,'axis_cartesian_relative_error_max':1e-9,
            'coordinate_tolerance_m':1e-8,'area_relative_error_max':1e-8,'volume_relative_error_max':1e-8,
            'aspect_ratio_max':10,'max_nonorthogonality_deg':1,'max_skewness':1},
        'topology':'Conformal axis-aligned six-quadrilateral-face hexahedra; mandatory visible macro cuts, uniform spacing within each macro interval. Exact raw box partition must cover the whole fluid domain once and solid interior zero times.',
        'directional_refinement':'For every x/y/z macro interval the actual integer count ratio medium/coarse and fine/medium must agree with the corresponding volume-derived r within 1% relative. Geometry and sample positions remain common.',
        'qualification_scope':'One fresh coarse-parent pilot precedes the three actual study grids; it is tool qualification, not a fourth benchmark case and is not used in the primary metric.',
        'software':'OpenCFD-v2412','mpi_ranks':16,'physical_cpus':'80-95','flow_solver_runs':0,
        'tool_sequence':['blockMesh','checkMesh','decomposePar','checkMesh.parallel'],
        'truth_boundary':'Local mesh-design/tool reference only; no CFD/LES solution, observed GCI, flow grid-independence, yplus or experimental truth is claimed.',
        'legal_alternatives':'Different positive integer count arrays and actual cell numbers satisfying all public constraints are valid. No GT cell identities or file equality is required.'}

def header(name):return f'FoamFile{{version 2.0; format ascii; class dictionary; object {name};}}\n'

def predicted_counts(counts):
    if len(counts)!=3 or any(len(a)!=len(x)-1 or any(isinstance(n,bool) or not isinstance(n,int) or n<1 for n in a) for a,x in zip(counts,CUTS)):raise ValueError('three positive integer macro count arrays required')
    n=sum(counts[0])*sum(counts[1])*sum(counts[2])-counts[0][1]*counts[1][1]*counts[2][0]
    maximum=[max((x[j+1]-x[j])/a[j] for j in range(len(a))) for a,x in zip(counts,CUTS)]
    return {'cells':n,'resolution_max_m':maximum}

def generate(destination,counts=None):
    destination=Path(destination);counts=counts or TEMPLATE;predicted_counts(counts)
    nx,ny,nz=[len(x) for x in CUTS];vertices=[(x,y,z) for z in CUTS[2] for y in CUTS[1] for x in CUTS[0]]
    index=lambda i,j,k:k*nx*ny+j*nx+i
    blocks=[];boundary={name:[] for name in ['inlet','outlet','sideLow','sideHigh','ground','top','building']}
    for k in range(nz-1):
        for j in range(ny-1):
            for i in range(nx-1):
                if (i,j,k)==(1,1,0):continue
                v=[index(i,j,k),index(i+1,j,k),index(i+1,j+1,k),index(i,j+1,k),index(i,j,k+1),index(i+1,j,k+1),index(i+1,j+1,k+1),index(i,j+1,k+1)]
                blocks.append('hex ('+' '.join(map(str,v))+') ('+' '.join(str(counts[d][q]) for d,q in enumerate((i,j,k)))+') simpleGrading (1 1 1)')
                face=[(v[0],v[4],v[7],v[3]),(v[1],v[2],v[6],v[5]),(v[0],v[1],v[5],v[4]),(v[3],v[7],v[6],v[2]),(v[0],v[3],v[2],v[1]),(v[4],v[5],v[6],v[7])]
                for name,edge,f in [('inlet',i==0,face[0]),('outlet',i==nx-2,face[1]),('sideLow',j==0,face[2]),('sideHigh',j==ny-2,face[3]),('ground',k==0,face[4]),('top',k==nz-2,face[5])]:
                    if edge:boundary[name].append(f)
                for neighbour,f in [((i-1,j,k),face[0]),((i+1,j,k),face[1]),((i,j-1,k),face[2]),((i,j+1,k),face[3]),((i,j,k-1),face[4]),((i,j,k+1),face[5])]:
                    if neighbour==(1,1,0):boundary['building'].append(f)
    s=header('blockMeshDict')+'convertToMeters 1;\nvertices (\n'+'\n'.join('('+' '.join(f'{p:g}' for p in v)+')' for v in vertices)+'\n);\nblocks (\n'+'\n'.join(blocks)+'\n);\nedges ();\nboundary (\n'
    for name,faces in boundary.items():s+=name+' {type '+('wall' if name in ['building','ground'] else 'patch')+'; faces ('+' '.join('('+' '.join(map(str,f))+')' for f in faces)+');}\n'
    s+=');\nmergePatchPairs ();\n'
    files={'system/blockMeshDict':s,'system/controlDict':header('controlDict')+'application blockMesh; startFrom startTime; startTime 0; stopAt endTime; endTime 1; deltaT 1; writeControl timeStep; writeInterval 1; writeFormat ascii; writePrecision 12; writeCompression off; runTimeModifiable false;\n',
        'system/fvSchemes':header('fvSchemes')+'ddtSchemes {default Euler;} gradSchemes {default Gauss linear;} divSchemes {default none;} laplacianSchemes {default Gauss linear corrected;} interpolationSchemes {default linear;} snGradSchemes {default corrected;}\n',
        'system/fvSolution':header('fvSolution')+'solvers {}\n','system/decomposeParDict':header('decomposeParDict')+'numberOfSubdomains 16; method scotch;\n'}
    for relative,value in files.items():
        p=destination/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value,encoding='utf-8',newline='\n')
    (destination/'mesh_counts.json').write_text(json.dumps(counts)+'\n',encoding='utf-8')
    (destination/'three_level_mesh_contract.json').write_text(json.dumps(contract(),ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('destination',type=Path);p.add_argument('--counts',type=Path);a=p.parse_args()
    if a.destination.exists():raise FileExistsError('fresh output directory required')
    generate(a.destination,json.loads(a.counts.read_text(encoding='utf-8')) if a.counts else None)
