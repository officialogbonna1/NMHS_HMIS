import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Every test gets a clean document. Without this a modal from one test is
// still mounted while the next one queries for its own.
afterEach(cleanup);
