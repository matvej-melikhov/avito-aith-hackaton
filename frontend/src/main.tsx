import { createRoot } from "react-dom/client";
import { App } from "./App";
import { Feedback } from "./Feedback";
import { ApiClient } from "./api/client";
import "./styles.css";
async function start() {
  const api = __DEMO__
    ? new ApiClient((await import("./mocks/transport")).createDemoTransport())
    : new ApiClient();
  createRoot(document.getElementById("root")!).render(
    <>
      <App api={api} demo={__DEMO__} />
      <Feedback />
    </>,
  );
}
void start();
