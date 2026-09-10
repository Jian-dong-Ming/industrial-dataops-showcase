# 停止任务状态保护

曾出现 desired_state=stopped、connection_state=reconnecting 的不一致状态。以下说明已复现的竞态机制与防护，不把历史观察当作原始触发事件已完全确定的证据。

## 可复现机制

旧`_connection_lost`无条件写reconnecting，通用状态更新和异常分支也未检查用户最新期望状态。停止之后的延迟回调可以覆盖stopped。监督器只处理它内存中拥有的runner，重启后不会收敛数据库中已停止但连接状态陈旧的任务。

## 修复

- 连接状态写入和故障记录先锁定任务行，核对最新desired_state；停止后拒绝旧回调写connected/reconnecting/error，重新启动后拒绝旧清理写stopped。
- 停止任务的迟到故障回调不再增加错误数或制造新心跳；处于running的真实故障继续记录。
- 监督器完成自己拥有的runner取消/排空后，收敛没有本地runner且desired_state=stopped的遗留连接状态；只更新时间和连接状态，不制造活跃心跳，不删除样本，不清空历史错误。
- 保持用户启停意图；只有 desired_state=running 的任务会被监督器重新建立连接。

## 测试边界

`test_late_disconnect_cannot_overwrite_stop_and_start`覆盖停止后迟到回调、异常和connected写入、停止遗留状态、拥有runner时不提前标停、重新启动后的迟到清理、running真实断连与缺失对象。原真实模拟器重启重连测试继续执行。

现架构仍是单监督器；本补丁不是多采集节点租约、主备选举或进程代际隔离机制。多个收集器并行运行需另外设计所有权与fencing，不能用这个补丁宣称已支持分布式采集。
