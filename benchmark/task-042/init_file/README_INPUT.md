# task-042 本地snappy输入

用 local_assets/generate_snappy_mesh_inputs.py 新目录 [--smoke] 重建完整STL和字典；初始背景网格不扣除建筑。公开网格/层/几何门禁、资格与正式设置在 local_assets/openfoam_base/snappy_mesh_contract.json。初始化不含任何网格结果、参考指标或求解器二进制，GT仍未生成。此任务只验证真实网格工具，不推断流场、y+或实验精度。
