# OpsLab MySQL Backup / Restore 验证报告

## 1. 验证目的

本次验证用于确认 OpsLab Kubernetes / SRE 项目中的 MySQL 不仅具备数据持久化能力，还具备经过真实验证的逻辑备份与恢复能力。

本次阶段的核心目标不是简单证明 `mysqldump` 可以执行，而是建立完整证据链：

```text
真实 MySQL 数据
    ↓
生成逻辑备份
    ↓
校验备份文件
    ↓
隔离环境恢复
    ↓
验证 Schema 与数据
    ↓
制造受控数据丢失
    ↓
执行单表恢复
    ↓
比较恢复前后数据一致性
    ↓
验证业务基线未受影响
```

最终目标：

```text
备份真实可生成
+
备份真实可恢复
+
恢复后数据与源数据一致
+
受控故障恢复不破坏业务基线
```

---

## 2. 环境基线

### 2.1 Kubernetes

```text
Cluster:
kubeadm Kubernetes v1.36.3

Namespace:
opslab
```

### 2.2 MySQL

```text
StatefulSet:
opslab-mysql

Pod:
opslab-mysql-0

Node:
k8s-worker1

MySQL Server:
8.4.10

mysql client:
8.4.10

mysqldump:
8.4.10
```

MySQL 数据通过 Local PV 持久化：

```text
PVC:
opslab-mysql-data

PV:
opslab-mysql-local-pv

Capacity:
18Gi

StorageClass:
local-storage
```

MySQL 容器数据目录：

```text
/var/lib/mysql
```

---

## 3. 数据库现状调查

正式备份前首先进行了只读调查。

当前数据库：

```text
information_schema
mysql
opslab
performance_schema
sys
```

`opslab` 初始主要表：

```text
opslab_events
sre_persistence_test
```

其中：

```text
opslab_events = 4 rows
```

数据库规模约：

```text
0.03 MiB
```

数据库中的业务表均使用：

```text
InnoDB
```

关键 MySQL 参数：

```text
character_set_server  = utf8mb4
collation_server      = utf8mb4_0900_ai_ci
gtid_mode             = OFF
log_bin               = ON
transaction_isolation = REPEATABLE-READ
```

因此本阶段选择使用基于 `mysqldump` 的逻辑备份方案。

---

## 4. 为什么第一版选择 mysqldump

当前 OpsLab 项目规模较小，本阶段目标是验证最基础但完整的 Backup / Restore 闭环。

因此第一版没有引入：

```text
XtraBackup
MySQL Operator
主从复制
PITR
对象存储
```

本阶段重点是：

```text
备份
→ 验证备份
→ 恢复
→ 验证恢复数据
```

而不是一次性构建完整生产级数据库灾备平台。

由于当前表均为 InnoDB，因此备份使用：

```text
--single-transaction
--quick
--skip-lock-tables
```

其中：

### `--single-transaction`

用于在 InnoDB 场景下获取事务一致性快照，降低逻辑备份期间长时间锁表的需求。

### `--quick`

用于逐行读取数据，而不是一次性将完整结果集加载进客户端内存。

当前：

```text
gtid_mode=OFF
```

因此明确使用：

```text
--set-gtid-purged=OFF
```

同时包含：

```text
--routines
--triggers
--events
```

保证逻辑全量备份不仅考虑普通表数据。

---

## 5. 备份故障域设计

MySQL 原始数据位于：

```text
k8s-worker1
→ Local PV
→ MySQL 数据磁盘
```

备份文件没有写入：

```text
/data/mysql
```

而是写入：

```text
k8s-control-plane
/home/rubio/backups/opslab/mysql/
```

数据路径：

```text
MySQL Pod
    ↓
mysqldump stdout
    ↓
kubectl exec
    ↓
control-plane shell redirect
    ↓
/home/rubio/backups/opslab/mysql/*.sql
```

因此：

```text
MySQL 原始数据
和
MySQL 逻辑备份
```

没有存放在同一块 Local PV 中。

这可以避免：

```text
MySQL 数据盘故障
        ↓
原始数据和备份同时丢失
```

但该方案仍然不是生产级异地备份。

如果 control-plane 与 worker1 同时发生严重故障，备份仍可能不可用。

---

## 6. 专用恢复验证对象

为了避免破坏正式业务表：

```text
opslab_events
```

本次专门创建：

```text
opslab.sre_backup_restore_test
```

测试数据固定为：

```text
1 | backup-test-001 | OpsLab MySQL Backup Restore Row 001
2 | backup-test-002 | OpsLab MySQL Backup Restore Row 002
3 | backup-test-003 | OpsLab MySQL Backup Restore Row 003
```

恢复前：

```text
row count = 3
```

对固定顺序的数据计算 SHA256：

```text
d5e646094426c0fcdb5f809f3332b90e1982e15d8fbf831ef2c0272b0227a20b
```

该值作为后续数据完整性验证基线。

---

## 7. 第一次整库逻辑备份

手工执行整库 `mysqldump`。

备份文件：

```text
/home/rubio/backups/opslab/mysql/opslab-20260813-044505.sql
```

执行结果：

```text
mysqldump_rc=0
MYSQL_BACKUP_CREATE=PASS
```

文件大小：

```text
4.1K
```

权限：

```text
600
```

备份文件 SHA256：

```text
e8295e03703bc296747ae8a3b75bcfb36002cd2d9df162d1b9951ef4b2fb8aa7
```

备份内容检查确认包含：

```text
opslab_events
sre_backup_restore_test
sre_persistence_test
```

并确认测试数据真实写入 SQL：

```text
backup-test-001
backup-test-002
backup-test-003
```

因此：

```text
MYSQL_BACKUP_CREATE=PASS
```

---

## 8. 整库隔离恢复验证

为了避免直接覆盖正在使用的：

```text
opslab
```

创建独立恢复数据库：

```text
opslab_restore_verify
```

数据路径：

```text
opslab-20260813-044505.sql
        ↓
kubectl exec -i
        ↓
mysql client
        ↓
opslab_restore_verify
```

恢复执行结果：

```text
MYSQL_LOGICAL_RESTORE=PASS
```

恢复后实际存在：

```text
opslab_events
sre_backup_restore_test
sre_persistence_test
```

测试表恢复数据：

```text
1 | backup-test-001 | OpsLab MySQL Backup Restore Row 001
2 | backup-test-002 | OpsLab MySQL Backup Restore Row 002
3 | backup-test-003 | OpsLab MySQL Backup Restore Row 003
```

---

## 9. 整库恢复数据一致性验证

恢复后同时计算源数据库和恢复数据库测试表的数据摘要。

结果：

```text
SOURCE
rows = 3
SHA256 = d5e646094426c0fcdb5f809f3332b90e1982e15d8fbf831ef2c0272b0227a20b
```

```text
RESTORED
rows = 3
SHA256 = d5e646094426c0fcdb5f809f3332b90e1982e15d8fbf831ef2c0272b0227a20b
```

因此：

```text
SOURCE_ROW_COUNT
=
RESTORED_ROW_COUNT
```

并且：

```text
SOURCE_DATA_SHA256
=
RESTORED_DATA_SHA256
```

正式判定：

```text
MYSQL_FULL_BACKUP_RESTORE=PASS
MYSQL_RESTORE_DATA_INTEGRITY=PASS
```

---

## 10. 单表备份

为了进一步模拟现实中的“误删单表”事故，又对：

```text
sre_backup_restore_test
```

生成专用单表 dump。

文件：

```text
/home/rubio/backups/opslab/mysql/sre_backup_restore_test-20260813-054043.sql
```

SHA256：

```text
d5baf5e51e4911c624891cc113762a64cb97b70199b26d1f81e628a1ea67cec9
```

检查确认 dump 中包含：

```text
sre_backup_restore_test
```

并包含三条预期测试数据。

同时检查 dump 不包含：

```text
opslab_events
sre_persistence_test
```

结果：

```text
TABLE_BACKUP_SCOPE=PASS
```

只有在 Scope Guard 通过之后，才允许进入受控删除实验。

---

## 11. 受控数据丢失故障注入

故障注入目标明确限定为：

```text
opslab.sre_backup_restore_test
```

正式业务表：

```text
opslab.opslab_events
```

禁止操作。

事故前：

```text
TEST_TABLE:
rows = 3
SHA256 = d5e646094426c0fcdb5f809f3332b90e1982e15d8fbf831ef2c0272b0227a20b

BUSINESS_TABLE:
opslab_events = 4 rows
```

执行：

```sql
DROP TABLE opslab.sre_backup_restore_test;
```

随后验证：

```text
sre_backup_restore_test exists = 0
```

同时：

```text
opslab_events = 4 rows
```

因此证明：

```text
故障确实发生
+
业务基线没有被破坏
```

正式记录：

```text
MYSQL_CONTROLLED_DATA_LOSS=PASS
MYSQL_BUSINESS_DATA_GUARD=PASS
```

---

## 12. 单表恢复

将此前经过 Scope Guard 验证的单表备份重新导入：

```text
opslab
```

恢复执行结果：

```text
table_restore_rc=0
MYSQL_TABLE_RESTORE=PASS
```

恢复后：

```text
sre_backup_restore_test exists = 1
```

恢复数据：

```text
1 | backup-test-001 | OpsLab MySQL Backup Restore Row 001
2 | backup-test-002 | OpsLab MySQL Backup Restore Row 002
3 | backup-test-003 | OpsLab MySQL Backup Restore Row 003
```

数据完整性：

```text
rows = 3

SHA256 =
d5e646094426c0fcdb5f809f3332b90e1982e15d8fbf831ef2c0272b0227a20b
```

与事故前完全一致。

同时：

```text
opslab_events = 4 rows
```

因此：

```text
MYSQL_TABLE_RESTORE=PASS
MYSQL_BUSINESS_DATA_GUARD=PASS
```

---

## 13. Backup / Restore 脚本工程化

验证完成后，将手工操作固化为：

```text
scripts/mysql-backup.sh
scripts/mysql-restore.sh
```

### 13.1 `mysql-backup.sh`

主要能力：

```text
数据库存在性与 Pod 可访问性检查
↓
拒绝将备份写入 Git 仓库
↓
创建 .partial 临时文件
↓
执行 mysqldump
↓
检查返回码
↓
检查文件非空
↓
原子重命名为正式 .sql
↓
chmod 600
↓
计算 SHA256
↓
输出 PASS
```

`.partial` 设计用于避免：

```text
mysqldump 中途失败
        ↓
留下一个名字看似正常
但实际只有部分内容的 .sql
```

---

## 14. `mysql-restore.sh` 安全设计

恢复脚本没有被设计成危险的：

```text
mysql opslab < backup.sql
```

而是提供两个明确模式。

### 14.1 `verify` 模式

用途：

```text
验证一份备份是否真实可恢复
```

安全规则：

```text
目标数据库必须不存在
```

如果数据库已存在：

```text
拒绝覆盖
```

同时拒绝包含：

```text
CREATE DATABASE
USE
```

等可能改变恢复目标范围的 SQL dump。

因此恢复默认模式是：

```text
Backup
    ↓
New isolated database
    ↓
Restore
    ↓
Validation
```

而不是直接修改正式数据库。

### 14.2 `table` 模式

用于受控单表恢复。

要求：

```text
dump 中必须恰好只有一个 Table structure
```

且：

```text
实际表名
=
EXPECTED_TABLE
```

同时拒绝数据库级：

```text
CREATE DATABASE
USE
```

语句。

最终还必须显式提供：

```text
--confirm
```

才能执行恢复。

---

## 15. 正式脚本真实验收

脚本语法验证：

```text
mysql-backup.sh syntax=PASS
mysql-restore.sh syntax=PASS
```

权限：

```text
755
```

使用正式：

```text
scripts/mysql-backup.sh
```

再次生成真实备份：

```text
/home/rubio/backups/opslab/mysql/opslab-20260813-061602.sql
```

文件 SHA256：

```text
735b50c35a321311e040801b0bd2d008c262761916b1dc9a9206dbefe15cf053
```

随后使用：

```text
scripts/mysql-restore.sh verify
```

恢复到：

```text
opslab_restore_script_061617
```

恢复后实际表：

```text
opslab_events
sre_backup_restore_test
sre_persistence_test
```

数据一致性：

```text
SOURCE
3
d5e646094426c0fcdb5f809f3332b90e1982e15d8fbf831ef2c0272b0227a20b
```

```text
SCRIPT_RESTORED
3
d5e646094426c0fcdb5f809f3332b90e1982e15d8fbf831ef2c0272b0227a20b
```

因此：

```text
MYSQL_BACKUP_SCRIPT=PASS
MYSQL_RESTORE_SCRIPT=PASS
MYSQL_SCRIPT_RESTORE_INTEGRITY=PASS
```

---

## 16. 已存在数据库覆盖保护验证

为了验证恢复脚本的安全护栏，故意再次尝试将同一备份恢复到已经存在的：

```text
opslab_restore_script_061617
```

脚本拒绝执行：

```text
ERROR: target database already exists: opslab_restore_script_061617
The verify mode refuses to overwrite existing databases.
```

退出码：

```text
1
```

这是预期失败。

因此：

```text
RESTORE_EXISTING_DATABASE_GUARD=PASS
```

证明安全护栏真实生效，而不是仅存在于脚本源码中。

---

## 17. 实验现场清理

全部恢复验证完成后，对临时实验对象进行了精确清理。

删除：

```text
opslab_restore_verify
opslab_restore_script_061617
opslab.sre_backup_restore_test
```

保留：

```text
opslab
opslab.opslab_events
opslab.sre_persistence_test
```

清理后查询：

```text
Temporary Restore Databases:
无
```

当前 `opslab` 表：

```text
opslab_events
sre_persistence_test
```

业务数据：

```text
opslab_events = 4 rows
```

既有持久化验证对象：

```text
sre_persistence_test exists = 1
```

因此：

```text
TEMP_RESTORE_DATABASE_CLEANUP=PASS
BACKUP_RESTORE_TEST_OBJECT_CLEANUP=PASS
MYSQL_BUSINESS_DATA_GUARD=PASS
MYSQL_EXISTING_VALIDATION_DATA_GUARD=PASS
```

---

## 18. 凭据安全

MySQL root 密码没有写入：

```text
Git
脚本
Validation 文档
```

密码继续由 Kubernetes Secret 注入 MySQL 容器。

验证过程中出现：

```text
mysql: [Warning] Using a password on the command line interface can be insecure.
```

这是因为当前脚本在 Pod 内使用：

```text
-p"${MYSQL_ROOT_PASSWORD}"
```

调用 MySQL CLI。

密码本身没有被输出到 Validation 文档或 Git，但这种调用方式仍会产生 MySQL CLI 安全警告。

这是当前方案的一个已知安全改进项。

后续如果继续强化，可改为更加严格的客户端凭据文件方案，而不是将密码作为 CLI 参数传入。

本阶段没有为了消除 warning 而扩大 Secret 暴露范围。

---

## 19. 本阶段证明的能力

本阶段已经真实证明：

- MySQL 逻辑全量备份：✅
- 备份文件非空检查：✅
- 备份文件 SHA256：✅
- 备份文件权限控制：✅
- 独立故障域保存备份：✅
- 整库隔离恢复：✅
- Schema 重建：✅
- 数据恢复：✅
- 恢复前后 Row Count 一致：✅
- 恢复前后 Data SHA256 一致：✅
- 误删单表故障注入：✅
- 单表恢复：✅
- 业务数据保护：✅
- 恢复脚本已有数据库覆盖保护：✅
- 实验现场清理：✅

---

## 20. 本阶段没有证明的能力

本阶段不能被描述成完整生产级 MySQL Disaster Recovery。

没有实现：

```text
PITR
```

没有验证：

```text
Binlog 回放恢复
```

没有实现：

```text
异地备份
对象存储备份
备份副本冗余
```

没有实现：

```text
自动 CronJob 定期备份
```

没有实现：

```text
MySQL 主从复制
自动 Failover
MySQL HA
```

没有验证：

```text
大规模数据库备份性能
大型数据库恢复时间
```

本阶段也没有正式测量：

```text
RPO
RTO
```

因此简历和答辩中不能声称已经实现：

```text
MySQL 高可用
生产级灾备
零数据丢失
分钟级 RTO
PITR
```

---

## 21. 当前方案的 SRE 价值

本阶段真正具有 SRE 项目价值的地方，不是：

```text
会使用 mysqldump
```

而是形成了：

```text
风险识别
    ↓
备份故障域分离
    ↓
真实备份
    ↓
完整性校验
    ↓
隔离恢复
    ↓
故障注入
    ↓
恢复
    ↓
恢复前后数据一致性证明
    ↓
业务数据 Guard
    ↓
恢复安全护栏
    ↓
现场清理
```

这说明项目已经从：

```text
“数据库能够运行”
```

推进到：

```text
“数据库发生部分数据丢失后，
存在经过验证的恢复路径”
```

这也是 Backup / Restore 阶段相对于单纯 Kubernetes Deployment Demo 的主要工程价值。

---

## 22. 最终验收

综合本次所有真实命令输出：

```text
MYSQL_BACKUP_CREATE=PASS
MYSQL_FULL_BACKUP_RESTORE=PASS
MYSQL_RESTORE_DATA_INTEGRITY=PASS
MYSQL_TABLE_BACKUP_CREATE=PASS
MYSQL_TABLE_BACKUP_SCOPE=PASS
MYSQL_CONTROLLED_DATA_LOSS=PASS
MYSQL_TABLE_RESTORE=PASS
MYSQL_BUSINESS_DATA_GUARD=PASS

MYSQL_BACKUP_SCRIPT=PASS
MYSQL_RESTORE_SCRIPT=PASS
MYSQL_SCRIPT_RESTORE_INTEGRITY=PASS
RESTORE_EXISTING_DATABASE_GUARD=PASS

TEMP_RESTORE_DATABASE_CLEANUP=PASS
BACKUP_RESTORE_TEST_OBJECT_CLEANUP=PASS
MYSQL_EXISTING_VALIDATION_DATA_GUARD=PASS
```

最终判定：

```text
MYSQL_BACKUP_RESTORE_VALIDATION=PASS
```

因此：

```text
OpsLab MySQL Backup / Restore
=
VALIDATED
```

本阶段可以进入 SEALED 状态。

后续除非发生直接涉及 MySQL Backup / Restore 的故障，否则不重新进行本次完整验证。
