# UrbanWind task055 v53 — 导入网格的内部面朝向诊断与最小修复

这是新的任务版本。给定的 `case/constant/polyMesh` 是本地合成16栋建筑城市的实际原生网格输入，0.25 m笛卡尔六面体，141952格、流体体积2218 m³。该输入包含一个内部面的顶点循环方向错误；点坐标、面所含顶点、单元连接和边界patch仍保留。请定位并修复，并用原始几何与真实检查证据说明为什么修复成立。

只能编辑 `faces` 中的顶点循环方向或循环起点。保持各face记录的顶点集合与循环相邻关系；`points/owner/neighbour/boundary` 不可改动。禁止删除、重网格、移动节点、交换owner/neighbour、放宽质量阈值或仅修改结果报告。允许任何等价的循环起点，无需匹配隐藏文件字节。此任务不提供正确网格、生成器或答案。

对每个内部面，由原始点重算面积矢量 S [m²]，从几何顶点重建相邻格中心，检查 S·(C_neighbour−C_owner)>0 [m³]。对每格报告 R=Σ sign·S [m²]，以及 ||R||/Σ||S|| [无量纲]。owner取+、neighbour取−。同时用固定测试向量 U=(1,0,0) m/s 计算 Q=U·R [m³/s] 和 Q/V_geometric [s⁻¹]。V_geometric由原始立方体几何计算；这些是几何守恒检查，不是流场求解。全域边界闭合不能替代逐格检查。

交付原始缺陷网格的诊断、最小修复后的网格、`repair_report.json` 和 `decision.md`。报告应列出所定位面及相邻单元的本实例编号，逐项修复前/后的原始量与单位、编辑范围、实际命令与完整日志/集合哈希、ACCEPT/REJECT/STOP及限制。

修复接受条件全部同时满足：剩余内部面朝向错误数0；全部单元面积闭合≤1e−6；几何、连接、实体排除和所有边界保持；完整 `checkMesh -constant -allGeometry -allTopology` 及完整公开质量检查通过。完整质量阈值在 `quality_thresholds.json`，包括nonorthogonality30°、internal/boundary skewness2及其他完整项。缺陷输入必须实际诊断为REJECT；工具超时、FatalError、缺失或截断证据是STOP。返回码0本身不表示网格通过。

评价仅执行可信检查工具与重建的静态字典，提交脚本、库、include和function objects不会执行。参考验收与评价器部署均尚未完成；这份输入本身不表示任务已成为reference-ready。
