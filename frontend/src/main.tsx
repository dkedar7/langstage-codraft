import "@fontsource/inter/300.css";
import "@fontsource/inter/400.css";
import "@fontsource/roboto-condensed/400.css";
import "@fontsource/roboto-condensed/700.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "@fontsource/inter/800.css";
import "@fontsource/source-serif-4/400.css";
import "@fontsource/source-serif-4/400-italic.css";
import "@fontsource/source-serif-4/600.css";
import "@fontsource/source-serif-4/700.css";
import "@fontsource/jetbrains-mono/400.css";
import "./styles.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./editor/App";
import { PrintView } from "./print/PrintView";
import { appPath } from "./base";

const path = appPath();
const print = path.match(/^\/print\/([\w-]+)/);

createRoot(document.getElementById("root")!).render(
  print ? <PrintView docId={print[1]} /> : <StrictMode><App /></StrictMode>,
);
