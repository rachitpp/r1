import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library's automatic cleanup hooks into a *global* `afterEach`, which
// we do not enable (`globals` is off). Without this, mounted trees survive
// between tests and `screen` queries start matching nodes from a previous one.
afterEach(cleanup);
