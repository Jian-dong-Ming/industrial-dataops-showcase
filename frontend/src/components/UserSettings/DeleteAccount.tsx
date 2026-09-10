import DeleteConfirmation from "./DeleteConfirmation"

const DeleteAccount = () => {
  return (
    <div className="max-w-md mt-4 rounded-lg border border-destructive/50 p-4">
      <h3 className="font-semibold text-destructive">删除账号</h3>
      <p className="mt-1 text-sm text-muted-foreground">
        永久删除当前账号及其关联数据，此操作无法撤销。
      </p>
      <DeleteConfirmation />
    </div>
  )
}

export default DeleteAccount
