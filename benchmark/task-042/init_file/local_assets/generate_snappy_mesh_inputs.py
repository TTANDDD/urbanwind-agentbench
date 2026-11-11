#!/usr/bin/env python3
"""Locally rebuild all task-042 STL/snappy inputs; no mesh result is generated."""
from pathlib import Path
import argparse
import json
import math

TASK_ID = 'urbanwind-task-042'
FORMULA = 'linear_percentile_95(unweighted Euclidean distance of each final building-patch face vertex-mean centre to the union of the 12 visible STL triangles); no face exclusion'


def contract():
    return {'schema_version': '1.0', 'task_id': TASK_ID, 'case_id': 'task-042-case-001',
            'metric': {'id': 'surface_conformance_p95_m', 'name_zh': '表面贴合距离P95', 'unit': 'm', 'formula': FORMULA},
            'geometry': {'building_bounds_m': [[-0.5,0.5],[-0.5,0.5],[0,4]],
                         'domain_bounds_m': [[-4,23],[-6.2,6.2],[0,9]],
                         'exposed_face_areas_m2': {'x_low':4,'x_high':4,'y_low':4,'y_high':4,'roof':1},
                         'fluid_volume_m3': 3009.2, 'source': 'locally generated closed 12-triangle STL'},
            'software': 'OpenCFD-v2412', 'mpi_ranks':16, 'physical_cpus':'64-79',
            'background': {'cell_counts':[54,25,18], 'building_subtracted_before_snappy':False},
            'qualification': {'surface_level':1,'feature_level':1,'near_box_level':1,'surface_layers':1},
            'full': {'surface_level':2,'feature_level':2,'near_box_level':1,'surface_layers':2},
            'shared': {'locationInMesh':[-3,1.3,1.1], 'relativeSizes':True, 'finalLayerThickness':0.3,
                       'expansionRatio':1.2, 'minThickness':0.05, 'nLayerIter':50,
                       'maxGlobalCells':800000, 'maxNonOrtho':65},
            'frozen_gates': {'surface_p95_max_m':0.01,'surface_distance_max_m':0.04,
                             'exposed_face_area_relative_error_max':0.05,'normal_alignment_min':0.98,
                             'layer_area_coverage_min':0.25,'layer_added_cells_min':1,
                             'fluid_volume_relative_error_max':0.005,'max_internal_building_cell_centres':0,
                             'cells_min':1000,'cells_max':800000,'min_cell_volume_m3':0,
                             'near_surface_cell_volume_max_m3':0.02,'max_nonorthogonality_deg':65,
                             'max_internal_skewness':4,'max_boundary_skewness':20},
            'percentile_rule': 'sort n distances, h=0.95*(n-1), interpolate floor(h)/ceil(h); uniform face weighting',
            'geometry_audit_rules': {
                'face_centre': 'arithmetic mean of all vertices of that raw polyMesh face',
                'exposed_face_assignment': 'nearest of x=-0.5, x=0.5, y=-0.5, y=0.5, z=4; ties use this order; all building faces included',
                'patch_normal': 'fluid outward normal points into the building: +x,-x,+y,-y,-z for the five faces',
                'near_surface_cells': 'all fluid cell volume-centres with shortest distance to source STL <=0.25 m',
                'solid_interior_tolerance_m': 1e-7,
                'layer_coverage': 'sum(building polygon areas with raw boundary nSurfaceLayers>=1)/sum(all building polygon areas)',
                'cell_volume_and_centre': 'oriented polygon triangle tetrahedra; owner positive and neighbour negative; no bounding-box cell approximation'
            },
            'layer_evidence': 'actual nSurfaceLayers building boundary area and internal positive cells, addedCells set, and raw snappy log; a request in the dictionary alone is not evidence',
            'required_tool_sequence':['surfaceCheck','blockMesh','checkMesh.background','surfaceFeatureExtract','decomposePar','snappyHexMesh','reconstructParMesh','reconstructPar','checkMesh.final'],
            'truth_boundary':'Local geometry and real OpenFOAM mesh tools only; zero flow-solver runs, no flow/yplus/experimental truth claimed.'}


def header(object_name, class_name='dictionary'):
    return f'FoamFile{{version 2.0; format ascii; class {class_name}; object {object_name};}}\n'


def stl():
    v=[(-.5,-.5,0),(.5,-.5,0),(.5,.5,0),(-.5,.5,0),(-.5,-.5,4),(.5,-.5,4),(.5,.5,4),(-.5,.5,4)]
    quads=[(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]
    lines=['solid building']
    for a,b,c,d in quads:
        for ids in ((a,b,c),(a,c,d)):
            p,q,r=[v[i] for i in ids]
            u,w=[q[i]-p[i] for i in range(3)],[r[i]-p[i] for i in range(3)]
            n=[u[1]*w[2]-u[2]*w[1],u[2]*w[0]-u[0]*w[2],u[0]*w[1]-u[1]*w[0]]
            length=math.sqrt(sum(x*x for x in n))
            lines += ['  facet normal '+' '.join(f'{x/length:g}' for x in n),'    outer loop']
            lines += ['      vertex '+' '.join(f'{x:g}' for x in v[i]) for i in ids]
            lines += ['    endloop','  endfacet']
    return '\n'.join(lines+['endsolid building',''])


def generate(destination, smoke=False):
    destination=Path(destination)
    c=contract(); stage=c['qualification' if smoke else 'full']; level=stage['surface_level']
    files={}
    files['constant/triSurface/building.stl']=stl()
    files['system/blockMeshDict']=header('blockMeshDict')+'''convertToMeters 1;
vertices ((-4 -6.2 0)(23 -6.2 0)(23 6.2 0)(-4 6.2 0)(-4 -6.2 9)(23 -6.2 9)(23 6.2 9)(-4 6.2 9));
blocks (hex (0 1 2 3 4 5 6 7) (54 25 18) simpleGrading (1 1 1));
edges ();
boundary
(
 inlet {type patch; faces ((0 4 7 3));}
 outlet {type patch; faces ((1 2 6 5));}
 sideLow {type patch; faces ((0 1 5 4));}
 sideHigh {type patch; faces ((3 7 6 2));}
 ground {type wall; faces ((0 3 2 1));}
 top {type patch; faces ((4 5 6 7));}
);
mergePatchPairs ();
'''
    files['system/controlDict']=header('controlDict')+'''application snappyHexMesh;
startFrom startTime; startTime 0; stopAt endTime; endTime 1; deltaT 1;
writeControl timeStep; writeInterval 1; writeFormat ascii; writePrecision 12;
writeCompression off; timeFormat general; timePrecision 12; runTimeModifiable false;
'''
    files['system/fvSchemes']=header('fvSchemes')+'''ddtSchemes {default Euler;}
gradSchemes {default Gauss linear;}
divSchemes {default none;}
laplacianSchemes {default Gauss linear corrected;}
interpolationSchemes {default linear;}
snGradSchemes {default corrected;}
'''
    files['system/fvSolution']=header('fvSolution')+'solvers {}\n'
    files['system/decomposeParDict']=header('decomposeParDict')+'numberOfSubdomains 16; method scotch;\n'
    files['system/surfaceFeatureExtractDict']=header('surfaceFeatureExtractDict')+'''building.stl
{
 extractionMethod extractFromSurface;
 extractFromSurfaceCoeffs {includedAngle 150;}
 writeObj yes;
}
'''
    files['system/snappyHexMeshDict']=header('snappyHexMeshDict')+f'''castellatedMesh true; snap true; addLayers true;
geometry
{{
 building.stl {{type triSurfaceMesh; name building;}}
 nearBox {{type searchableBox; min (-1.5 -1.5 0); max (1.5 1.5 5);}}
}}
castellatedMeshControls
{{
 maxLocalCells 100000; maxGlobalCells 800000; minRefinementCells 0;
 maxLoadUnbalance 0.1; nCellsBetweenLevels 3;
 features ({{file "building.eMesh"; level {stage['feature_level']};}});
 refinementSurfaces {{building {{level ({level} {level}); patchInfo {{type wall;}}}}}}
 resolveFeatureAngle 30;
 refinementRegions {{nearBox {{mode inside; levels ((1E15 {stage['near_box_level']}));}}}}
 locationInMesh (-3 1.3 1.1); allowFreeStandingZoneFaces true;
}}
snapControls
{{
 nSmoothPatch 3; tolerance 2.0; nSolveIter 30; nRelaxIter 5;
 nFeatureSnapIter 10; implicitFeatureSnap false; explicitFeatureSnap true; multiRegionFeatureSnap false;
}}
addLayersControls
{{
 relativeSizes true;
 layers {{building {{nSurfaceLayers {stage['surface_layers']};}} ground {{nSurfaceLayers 0;}}}}
 expansionRatio 1.2; finalLayerThickness 0.3; minThickness 0.05;
 nGrow 0; featureAngle 120; nRelaxIter 5; nSmoothSurfaceNormals 1;
 nSmoothNormals 3; nSmoothThickness 10; maxFaceThicknessRatio 0.5;
 maxThicknessToMedialRatio 0.3; minMedialAxisAngle 90;
 nBufferCellsNoExtrude 0; nLayerIter 50; nRelaxedIter 20;
}}
meshQualityControls
{{
 maxNonOrtho 65; maxBoundarySkewness 20; maxInternalSkewness 4;
 maxConcave 80; minVol 1e-13; minTetQuality 1e-15; minArea -1;
 minTwist 0.02; minDeterminant 0.001; minFaceWeight 0.05;
 minVolRatio 0.01; minTriangleTwist -1; nSmoothScale 4; errorReduction 0.75;
 relaxed {{maxNonOrtho 65;}}
}}
writeFlags (scalarLevels layerSets layerFields);
mergeTolerance 1e-6;
'''
    quality=files['system/snappyHexMeshDict'].split('meshQualityControls\n{',1)[1].split('\nwriteFlags',1)[0].rsplit('}',1)[0]
    files['system/meshQualityDict']=header('meshQualityDict')+quality
    for relative,text in files.items():
        path=destination/relative; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(text,encoding='utf-8',newline='\n')
    (destination/'snappy_mesh_contract.json').write_text(json.dumps(c,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return files


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('destination',type=Path); parser.add_argument('--smoke',action='store_true')
    args=parser.parse_args()
    if args.destination.exists():
        raise FileExistsError('Fresh destination required: '+str(args.destination))
    generate(args.destination,args.smoke)
