#!/usr/bin/env python3
"""Public coarse Cartesian template. Users supply their own directional counts."""
from pathlib import Path
import argparse,json

FORMULA='sum(exact cell-box intersection volume with visible wake ROI for cells satisfying all visible dx/dy/dz and anisotropy limits)/analytic wake ROI volume; raw Cartesian topology must tile fluid domain without gaps or overlaps'
CUTS=[[-4,-.5,.5,4.5,8.5,23],[-6.2,-1.5,-.5,.5,1.5,6.2],[0,4,6,9]]
COARSE=[[7,2,8,8,29],[10,2,2,2,10],[8,4,6]]

def contract():
    return {'schema_version':'1.0','task_id':'urbanwind-task-044','case_id':'task-044-case-001',
        'metric':{'id':'wake_refinement_coverage','name_zh':'尾流加密区覆盖率','unit':'fraction','formula':FORMULA},
        'geometry':{'domain_bounds_m':[[-4,23],[-6.2,6.2],[0,9]],'building_bounds_m':[[-.5,.5],[-.5,.5],[0,4]],
                    'wake_roi_m':[[.5,8.5],[-1.5,1.5],[0,6]],'wake_volume_m3':144.0,'fluid_volume_m3':3009.2,
                    'building_exposed_areas_m2':{'x_low':4,'x_high':4,'y_low':4,'y_high':4,'roof':1}},
        'full':{'resolution_max_m':[.125,.25,.25],'dy_over_dx_min':1.8,'dz_over_dx_min':1.8},
        'qualification':{'resolution_max_m':[.25,.5,.5],'dy_over_dx_min':1.8,'dz_over_dx_min':1.8},
        'gates':{'wake_coverage_min':.99999999,'outside_coarse_volume_fraction_min':.70,'outside_coarse_dx_min_m':.4,
                 'cells_min':1000,'cells_max':200000,'atomic_grid_voxels_max':2000000,'axis_cartesian_relative_error_max':1e-9,
                 'patch_absolute_error_m':1e-8,'area_relative_error_max':1e-8,'volume_relative_error_max':1e-8,
                 'aspect_ratio_max':10,'max_nonorthogonality_deg':1,'max_skewness':1},
        'topology':'Conformal axis-aligned rectangular hexahedra, six planar quadrilateral faces per cell; exact raw box partition proof required.',
        'measurement':'Cell bounds from all owner/neighbour face vertices; verify actual volume against bbox volume, then exact intersection products. Atomic interval occupancy must be 1 outside solid and 0 inside, not merely match global volume. Only atomic plane indexing rounds coordinates to 1e-10 m to reconcile ASCII roundoff; intersection volumes use raw coordinates.',
        'tolerance':'Resolution and ratio comparisons use relative tolerance 1e-9; metric remains exact summed overlap volume/144.',
        'software':'OpenCFD-v2412','mpi_ranks':16,'physical_cpus':'64-79','flow_solver_runs':0,
        'tool_sequence':['blockMesh','checkMesh','decomposePar','checkMesh.parallel'],
        'truth_boundary':'Local geometric mesh-tool reference only. No velocity solution, wake accuracy, yplus, experimental or atmospheric truth is claimed.',
        'legal_alternatives':'Different count/layout choices satisfying all public constraints are valid; byte-identical meshes, cell IDs and reference cell counts are not required.'}

def header(name):return f'FoamFile{{version 2.0; format ascii; class dictionary; object {name};}}\n'

def generate(destination,counts=None):
    destination=Path(destination); counts=counts or COARSE
    if any(len(c)!=len(x)-1 or any(isinstance(n,bool) or not isinstance(n,int) or n<1 for n in c) for c,x in zip(counts,CUTS)) or len(counts)!=3:raise ValueError('three positive integer count arrays required')
    nx,ny,nz=[len(x) for x in CUTS]
    vertices=[(x,y,z) for z in CUTS[2] for y in CUTS[1] for x in CUTS[0]]
    index=lambda i,j,k:k*nx*ny+j*nx+i
    kept=[];blocks=[];boundary={name:[] for name in ['inlet','outlet','sideLow','sideHigh','ground','top','building']}
    for k in range(nz-1):
        for j in range(ny-1):
            for i in range(nx-1):
                if (i,j,k)==(1,2,0):continue
                v=[index(i,j,k),index(i+1,j,k),index(i+1,j+1,k),index(i,j+1,k),index(i,j,k+1),index(i+1,j,k+1),index(i+1,j+1,k+1),index(i,j+1,k+1)]
                blocks.append('hex ('+' '.join(map(str,v))+') ('+' '.join(str(counts[d][q]) for d,q in enumerate((i,j,k)))+') simpleGrading (1 1 1)')
                face=[(v[0],v[4],v[7],v[3]),(v[1],v[2],v[6],v[5]),(v[0],v[1],v[5],v[4]),(v[3],v[7],v[6],v[2]),(v[0],v[3],v[2],v[1]),(v[4],v[5],v[6],v[7])]
                for name,edge,f in [('inlet',i==0,face[0]),('outlet',i==nx-2,face[1]),('sideLow',j==0,face[2]),('sideHigh',j==ny-2,face[3]),('ground',k==0,face[4]),('top',k==nz-2,face[5])]:
                    if edge:boundary[name].append(f)
                for neighbour,f in [((i-1,j,k),face[0]),((i+1,j,k),face[1]),((i,j-1,k),face[2]),((i,j+1,k),face[3]),((i,j,k-1),face[4]),((i,j,k+1),face[5])]:
                    if neighbour==(1,2,0):boundary['building'].append(f)
    s=header('blockMeshDict')+'convertToMeters 1;\nvertices (\n'+'\n'.join('('+' '.join(f'{p:g}' for p in v)+')' for v in vertices)+'\n);\nblocks (\n'+'\n'.join(blocks)+'\n);\nedges ();\nboundary (\n'
    for name,faces in boundary.items():
        kind='wall' if name in ['building','ground'] else 'patch'
        s+=name+' {type '+kind+'; faces ('+' '.join('('+' '.join(map(str,f))+')' for f in faces)+');}\n'
    s+=');\nmergePatchPairs ();\n'
    files={'system/blockMeshDict':s,'system/controlDict':header('controlDict')+'application blockMesh; startFrom startTime; startTime 0; stopAt endTime; endTime 1; deltaT 1; writeControl timeStep; writeInterval 1; writeFormat ascii; writePrecision 12; writeCompression off; runTimeModifiable false;\n',
        'system/fvSchemes':header('fvSchemes')+'ddtSchemes {default Euler;} gradSchemes {default Gauss linear;} divSchemes {default none;} laplacianSchemes {default Gauss linear corrected;} interpolationSchemes {default linear;} snGradSchemes {default corrected;}\n',
        'system/fvSolution':header('fvSolution')+'solvers {}\n','system/decomposeParDict':header('decomposeParDict')+'numberOfSubdomains 16; method scotch;\n'}
    for relative,value in files.items():
        p=destination/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value,encoding='utf-8',newline='\n')
    (destination/'mesh_counts.json').write_text(json.dumps(counts)+'\n',encoding='utf-8')
    (destination/'wake_mesh_contract.json').write_text(json.dumps(contract(),ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('destination',type=Path);p.add_argument('--counts',type=Path);a=p.parse_args()
    if a.destination.exists():raise FileExistsError('fresh output directory required')
    generate(a.destination,json.loads(a.counts.read_text(encoding='utf-8')) if a.counts else None)
