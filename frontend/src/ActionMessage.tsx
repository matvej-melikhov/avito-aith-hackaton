import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react";
import { Btn, Callout } from "./ds";

const Context = createContext({
  message: "",
  show: (_message: string) => {},
  clear: () => {},
});

export function ActionMessageProvider({ children }: { children: ReactNode }) {
  const [message, show] = useState("");
  const clear = useCallback(() => show(""), []);
  return (
    <Context.Provider value={{ message, show, clear }}>
      {children}
    </Context.Provider>
  );
}

export function useActionMessage() {
  return useContext(Context);
}

export function ActionMessage() {
  const { message, clear } = useActionMessage();
  return message ? (
    <Callout tone="info" role="status" className="feedback action-message">
      <p>{message}</p>
      <Btn size="s" variant="quiet" onClick={clear}>
        Закрыть сообщение
      </Btn>
    </Callout>
  ) : null;
}
