import "@mantine/core/styles.css";

import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";

// Everything the page shows is asked of the API, and nothing is kept between
// visits: a failed request says so once, where a retry would only delay it.
const queries = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MantineProvider defaultColorScheme="auto">
      <QueryClientProvider client={queries}>
        <App />
      </QueryClientProvider>
    </MantineProvider>
  </StrictMode>,
);
