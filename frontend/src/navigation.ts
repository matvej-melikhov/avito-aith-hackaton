const dirtyEditors = new Set<symbol>();
let programmed = false;
export function registerDirtyEditor() {
  const key = Symbol();
  dirtyEditors.add(key);
  return () => {
    dirtyEditors.delete(key);
  };
}
export function confirmNavigation() {
  return (
    dirtyEditors.size === 0 ||
    window.confirm("Есть несохранённые изменения. Покинуть страницу?")
  );
}
export function markProgrammaticNavigation() {
  programmed = true;
}
export function consumeProgrammaticNavigation() {
  const value = programmed;
  programmed = false;
  return value;
}
