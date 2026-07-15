# 管理用户更新原子性设计

## 目标

将角色、状态和 `auth.admin.manage` 直接委派收敛到同一 SQLite 事务，避免管理端更新出现半完成状态。

## 设计

授权判断保留在 `AuthService.update_managed_user`；`SQLiteAuthStore.update_user` 增加可选的直接权限变更参数，并在既有连接中完成用户字段、角色和权限写入。任一校验或 SQL 写入失败时，`_ManagedConnection` 回滚整笔变更。

## 验收

使用不存在的直接权限强制后半段失败，断言角色更新不会提交。
