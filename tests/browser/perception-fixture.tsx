import React from "react";
import { createRoot } from "react-dom/client";
import RiderPage from "../../web-next/page";
import { Host } from "./workspace-host";

createRoot(document.getElementById("root")!).render(<Host><RiderPage/></Host>);
