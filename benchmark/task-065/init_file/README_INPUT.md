# urbanwind-task-065: 合成湍流入口

本初始化包包含完全本地、可见并可重复生成的合成湍流入口合同。`synthetic_inflow_contract.json`冻结平均速度、三分量RMS、五个频带、随机种子、空间点阵、统计窗和可见门禁；`generate_synthetic_inflow.py`把合同转换为OpenFOAM `timeVaryingMappedFixedValue` boundaryData；`synthetic_inflow_probe_points.csv`冻结16个入口内侧与16个下游核验点。

主指标为 `inflow_spectral_band_error`：mean_{p in 16 inlet probes,c in Ux Uy Uz,b in 5 bands} abs(F_realized[p,c,b]-F_target[p,c,b]); F is normalized one-sided PSD energy over 30<t<=90 s at dt=0.2 s。目标输入与真实OpenFOAM原始探针必须分别重算；不得用生成器自报值、报告文字或任意有限数代替求解器证据。已安装匹配OpenFOAM数值参考及原始证据评价器；实际原始结果不在初始化包内。

这些输入是固定种子的本地合成边界，不是大气实测、实验、PIV、DNS、风洞或现场真值，也不代表通用ABL入口推荐。
