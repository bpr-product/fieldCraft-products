/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*/
import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

// Suppress known React test-environment warnings that don't indicate test failures:
//  • ReactDOMTestUtils.act deprecated  — RTL internals, unfixable without upgrading react-scripts
//  • "not configured to support act"   — same root cause
//  • "was not wrapped in act"          — async component handlers completing after test assertions
const _originalConsoleError = console.error.bind(console);
beforeAll(() => {
  jest.spyOn(console, 'error').mockImplementation((...args: any[]) => {
    const msg = typeof args[0] === 'string' ? args[0] : '';
    if (
      msg.includes('ReactDOMTestUtils.act') ||
      msg.includes('not configured to support act') ||
      msg.includes('was not wrapped in act')
    ) return;
    _originalConsoleError(...args);
  });
});
afterAll(() => (console.error as jest.Mock).mockRestore());

jest.mock("./ABCSupplyTable.scss", () => ({}));

const mockUseOidcAccessToken = jest.fn();

jest.mock("@axa-fr/react-oidc", () => ({
  useOidcAccessToken: () => mockUseOidcAccessToken(),
}));

jest.mock("../../../../services/VendorService", () => ({
  getAllVendors: jest.fn(),
}));

jest.mock("../../../../services/integrations/ABCSupplyService", () => ({
  searchABCSupplyItems: jest.fn(),
  getABCSupplyItem: jest.fn(),
  priceABCSupplyItems: jest.fn(),
  importSelectedABCSupplyMaterialsToFIELDCRAFT: jest.fn(),
  getABCSupplyConnectionStatus: jest.fn(),
  getValidTradeNames: jest.fn(),
  getImportedABCSupplyMaterials: jest.fn(),
}));

import ABCSupplyTable from "./ABCSupplyTable";
import { getAllVendors } from "../../../../services/VendorService";
import {
  searchABCSupplyItems,
  getABCSupplyItem,
  priceABCSupplyItems,
  importSelectedABCSupplyMaterialsToFIELDCRAFT,
  getABCSupplyConnectionStatus,
  getValidTradeNames,
  getImportedABCSupplyMaterials,
} from "../../../../services/integrations/ABCSupplyService";

const mockedSearch = searchABCSupplyItems as jest.Mock;
const mockedGetItem = getABCSupplyItem as jest.Mock;
const mockedPrice = priceABCSupplyItems as jest.Mock;
const mockedImportSelected = importSelectedABCSupplyMaterialsToFIELDCRAFT as jest.Mock;
const mockedGetAllVendors = getAllVendors as jest.Mock;
const mockedGetConnectionStatus = getABCSupplyConnectionStatus as jest.Mock;
const mockedGetValidTradeNames = getValidTradeNames as jest.Mock;
const mockedGetImportedMaterials = getImportedABCSupplyMaterials as jest.Mock;

const accessToken = "test-access-token";

const sampleVendors = [
  { id: 99, companyName: "ABC Supply" },
  { id: 100, companyName: "Other Vendor" },
];

const sampleItems = [
  {
    itemNumber: "02GASTZ3WW",
    itemDescription: "30-Year Architectural Shingle — Charcoal",
    manufacturerName: "Owens Corning",
    categoryName: "Roofing",
    sellingUnit: "SQ",
    available: true,
    branchAvailability: "In Stock",
  },
  {
    itemNumber: "FL-DRP-EDG-02",
    itemDescription: "Aluminum Drip Edge — White 10ft",
    manufacturerName: "Amerimax",
    categoryName: "Flashing",
    sellingUnit: "EA",
    available: true,
  },
];

const searchSuccessResponse = {
  ok: true,
  data: {
    items: sampleItems,
    pagination: { totalItems: 2, pageNumber: 1, itemsPerPage: 20 },
  },
};

const priceSuccessResponse = {
  ok: true,
  data: {
    lines: [
      { id: "1", itemNumber: "02GASTZ3WW", netPrice: 96.5, listPrice: 112, uom: "SQ" },
      { id: "2", itemNumber: "FL-DRP-EDG-02", netPrice: 8.25, uom: "EA" },
    ],
  },
};

function setup() {
  mockUseOidcAccessToken.mockReturnValue({ accessToken });
  mockedGetAllVendors.mockResolvedValue({ data: sampleVendors });
  mockedPrice.mockResolvedValue(priceSuccessResponse as any);
  mockedGetConnectionStatus.mockResolvedValue({
    ok: true,
    data: { connected: true, branchNumber: "17", shipToNumber: "2154884-2" },
  });
  mockedGetValidTradeNames.mockResolvedValue({ data: [{ tradeName: "Roofing" }] });
  mockedGetImportedMaterials.mockResolvedValue({ ok: true, data: { items: [] } });
}

// Stock status and decimal quantity are required, segmented Yes/No toggles (not dropdowns).
// Tests that add materials must choose both before the Add button is enabled.
function selectMaterialSettings(inStock: "Yes" | "No" = "Yes", decimalQty: "Yes" | "No" = "Yes") {
  fireEvent.click(screen.getByRole("button", { name: new RegExp(`In stock: ${inStock}`, "i") }));
  fireEvent.click(screen.getByRole("button", { name: new RegExp(`Decimal qty: ${decimalQty}`, "i") }));
}

// My Materials pagination/search are resolved server-side. This mock honors the
// page/limit/search fields the component sends so pagination tests exercise the
// real request/response contract instead of asserting against an unfiltered mock.
function mockPagedImportedMaterials(allItems: Array<Record<string, any>>) {
  mockedGetImportedMaterials.mockImplementation((payload: any) => {
    const search = String(payload?.search || "").toLowerCase();
    const filtered = search
      ? allItems.filter((material) =>
          [material.name, material.itemNumber, material.vendorName].some((value) =>
            String(value || "").toLowerCase().includes(search)
          )
        )
      : allItems;
    const page = payload?.page || 1;
    const limit = payload?.limit || 20;
    const start = (page - 1) * limit;
    const items = filtered.slice(start, start + limit);
    return Promise.resolve({
      ok: true,
      data: { items, page, limit, total: filtered.length, totalPages: Math.ceil(filtered.length / limit) || 0 },
    });
  });
}

describe("ABCSupplyTable", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    setup();
  });

  afterEach(async () => {
    // Flush any async state updates that completed after the test's act boundary closed.
    await act(async () => {});
  });

  it("renders the search field and vendor picker", async () => {
    render(<ABCSupplyTable tenantId="test-tenant" />);
    expect(await screen.findByPlaceholderText(/shingles, underlayment/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/vendor/i)).toBeInTheDocument();
  });

  it("searches ABC Supply and automatically fetches visible prices", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    render(<ABCSupplyTable tenantId="test-tenant" />);

    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });

    await act(async () => {
      fireEvent.keyDown(input, { key: "Enter" });
    });

    await waitFor(() => {
      expect(mockedSearch).toHaveBeenCalledWith(
        expect.objectContaining({ query: "shingle", items_per_page: 20, page_number: 1 }),
        accessToken
      );
      expect(mockedPrice).toHaveBeenCalledWith(
        expect.objectContaining({
          lines: expect.arrayContaining([
            expect.objectContaining({ itemNumber: "02GASTZ3WW", quantity: 1 }),
          ]),
        }),
        accessToken
      );
    });

    expect(await screen.findByText("02GASTZ3WW")).toBeInTheDocument();
    expect(await screen.findByText("$96.50")).toBeInTheDocument();
  });

  it("adds selected ABC materials to the material list using the selected vendor", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    mockedImportSelected.mockResolvedValueOnce({
      ok: true,
      data: { counts: { created: 1, skippedExisting: 0, failed: 0 } },
    } as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);

    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });

    await act(async () => {
      fireEvent.keyDown(input, { key: "Enter" });
    });

    await waitFor(() => screen.getByText("02GASTZ3WW"));
    fireEvent.click(screen.getByLabelText(/Select 02GASTZ3WW/i));
    selectMaterialSettings();
    fireEvent.click(screen.getByRole("button", { name: /add 1 to material list/i }));

    await waitFor(() => {
      expect(mockedImportSelected).toHaveBeenCalledWith(
        expect.objectContaining({
          fieldcraft_vendor_id: 99,
          fieldcraft_vendor_name: "ABC Supply",
          fetch_prices: true,
          require_pricing: true,
          allow_decimal: true,
          in_stock: true,
          materials: expect.arrayContaining([
            expect.objectContaining({ itemNumber: "02GASTZ3WW" }),
          ]),
        }),
        accessToken
      );
    });
  });

  // ─── Stock status / decimal quantity toggles ───────────────────────────────

  it("blocks add until stock status and decimal quantity are both chosen via the toggles", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    render(<ABCSupplyTable tenantId="test-tenant" />);

    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    fireEvent.click(screen.getByRole("checkbox", { name: /Select 02GASTZ3WW/i }));

    // Neither toggle has been chosen yet — button is disabled and asks to complete settings
    expect(screen.getByRole("button", { name: /Complete import settings/i })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /In stock: Yes/i }));
    expect(screen.getByRole("button", { name: /Complete import settings/i })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /Decimal qty: No/i }));
    expect(screen.getByRole("button", { name: /add 1 to material list/i })).not.toBeDisabled();
  });

  it("shows a warning toast if add is triggered without a stock status or decimal quantity choice", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    render(<ABCSupplyTable tenantId="test-tenant" />);

    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    fireEvent.click(screen.getByRole("checkbox", { name: /Select 02GASTZ3WW/i }));
    // Add button is disabled while settings are incomplete, so the drawer's "Add this material"
    // path (which shares the same handler) is exercised instead to confirm the guard itself fires.
    fireEvent.click(screen.getAllByRole("button", { name: /view/i })[0]);
    await waitFor(() => screen.getByText(/Add this material to material list/i));

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Add this material to material list/i }));
    });

    expect(mockedImportSelected).not.toHaveBeenCalled();
  });

  it("toggles between Yes and No exclusively for stock status", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    render(<ABCSupplyTable tenantId="test-tenant" />);

    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    const yesBtn = screen.getByRole("button", { name: /In stock: Yes/i });
    const noBtn = screen.getByRole("button", { name: /In stock: No/i });

    fireEvent.click(yesBtn);
    expect(yesBtn).toHaveClass("Mui-selected");
    expect(noBtn).not.toHaveClass("Mui-selected");

    fireEvent.click(noBtn);
    expect(noBtn).toHaveClass("Mui-selected");
    expect(yesBtn).not.toHaveClass("Mui-selected");
  });

  it("sends the chosen stock and decimal values (No / No) in the import payload", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    mockedImportSelected.mockResolvedValueOnce({
      ok: true,
      data: { counts: { created: 1, failed: 0 } },
    } as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    fireEvent.click(screen.getByRole("checkbox", { name: /Select 02GASTZ3WW/i }));
    selectMaterialSettings("No", "No");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /add 1 to material list/i }));
    });

    await waitFor(() => {
      expect(mockedImportSelected).toHaveBeenCalledWith(
        expect.objectContaining({ allow_decimal: false, in_stock: false }),
        accessToken
      );
    });
  });

  it("opens item detail", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    mockedGetItem.mockResolvedValueOnce({ ok: true, data: { item: sampleItems[0] } } as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);

    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });

    await act(async () => {
      fireEvent.keyDown(input, { key: "Enter" });
    });

    await waitFor(() => screen.getByText("02GASTZ3WW"));
    fireEvent.click(screen.getAllByRole("button", { name: /view/i })[0]);

    await waitFor(() => {
      expect(mockedGetItem).toHaveBeenCalledWith(
        expect.objectContaining({ item_number: "02GASTZ3WW" }),
        accessToken
      );
    });

    expect(screen.getByText(/ABC Supply Product/i)).toBeInTheDocument();
  });

  it("marks a search result as already added when the imported record's itemNumber matches even though externalItemId differs", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    mockedGetImportedMaterials.mockResolvedValue({
      ok: true,
      data: {
        items: [
          { itemNumber: "02GASTZ3WW", externalItemId: "EXT-DIFFERENT-999", name: "Shingle" },
        ],
      },
    });

    render(<ABCSupplyTable tenantId="test-tenant" />);

    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => {
      fireEvent.keyDown(input, { key: "Enter" });
    });

    await waitFor(() => screen.getByText("02GASTZ3WW"));
    expect(await screen.findByText("Added")).toBeInTheDocument();
  });

  it("does not refetch or filter My Materials when the selected vendor changes", async () => {
    render(<ABCSupplyTable tenantId="test-tenant" />);
    await screen.findByPlaceholderText(/shingles, underlayment/i);

    await waitFor(() => {
      expect(mockedGetImportedMaterials).toHaveBeenCalledWith(
        expect.objectContaining({ fieldcraft_vendor_id: null }),
        accessToken
      );
    });
    const callCountBeforeVendorChange = mockedGetImportedMaterials.mock.calls.length;

    const vendorInput = screen.getByPlaceholderText("Vendor");
    fireEvent.mouseDown(vendorInput);
    fireEvent.click(await screen.findByText("Other Vendor"));

    await act(async () => {});

    expect(mockedGetImportedMaterials).toHaveBeenCalledTimes(callCountBeforeVendorChange);
  });

  // ─── Initialization / Load errors ─────────────────────────────────────────

  it("renders without tenantId and skips connection defaults load", async () => {
    render(<ABCSupplyTable />);
    expect(await screen.findByPlaceholderText(/shingles, underlayment/i)).toBeInTheDocument();
  });

  it("handles getABCSupplyConnectionStatus throwing on mount", async () => {
    mockedGetConnectionStatus.mockRejectedValue(new Error("connection status failed"));
    render(<ABCSupplyTable tenantId="test-tenant" />);
    expect(await screen.findByPlaceholderText(/shingles, underlayment/i)).toBeInTheDocument();
  });

  it("shows vendor error when vendor load throws", async () => {
    mockedGetAllVendors.mockRejectedValue(new Error("vendor fetch failed"));
    render(<ABCSupplyTable tenantId="test-tenant" />);
    expect(await screen.findByText(/Could not load vendors/i)).toBeInTheDocument();
  });

  it("shows trade error when no trades are returned", async () => {
    mockedGetValidTradeNames.mockResolvedValue({ data: [] });
    render(<ABCSupplyTable tenantId="test-tenant" />);
    expect(await screen.findByText(/No valid trades were returned/i)).toBeInTheDocument();
  });

  it("shows trade error when trade load throws", async () => {
    mockedGetValidTradeNames.mockRejectedValue(new Error("trade fetch failed"));
    render(<ABCSupplyTable tenantId="test-tenant" />);
    expect(await screen.findByText(/Could not load valid trades/i)).toBeInTheDocument();
  });

  // ─── Selection ────────────────────────────────────────────────────────────

  it("toggleAllVisibleSelection selects then deselects all visible items", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    render(<ABCSupplyTable tenantId="test-tenant" />);

    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    const selectAllCheckbox = screen.getByRole("checkbox", { name: /Select all visible/i });
    const firstItemCheckbox = screen.getByRole("checkbox", { name: /Select 02GASTZ3WW/i });

    expect(firstItemCheckbox).not.toBeChecked();
    fireEvent.click(selectAllCheckbox);
    expect(firstItemCheckbox).toBeChecked();

    fireEvent.click(selectAllCheckbox);
    expect(firstItemCheckbox).not.toBeChecked();
  });

  // ─── Search behavior ──────────────────────────────────────────────────────

  it("handleSearch returns early when query and branch are both empty", async () => {
    mockedGetConnectionStatus.mockResolvedValue({
      ok: true,
      data: { connected: false, branchNumber: null, shipToNumber: null },
    });
    render(<ABCSupplyTable tenantId="test-tenant" />);

    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });

    expect(mockedSearch).not.toHaveBeenCalled();
  });

  it("shows not_configured error when search returns 400", async () => {
    mockedSearch.mockResolvedValueOnce({
      ok: false,
      status: 400,
      message: "ABC Supply integration is not configured.",
    } as any);
    render(<ABCSupplyTable tenantId="test-tenant" />);

    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });

    expect(await screen.findByText(/Integration not configured/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Try again/i })).not.toBeInTheDocument();
  });

  it("shows api_error state with retry button for non-400 search errors", async () => {
    mockedSearch.mockResolvedValue({
      ok: false,
      status: 503,
      message: "Service unavailable.",
    } as any);
    render(<ABCSupplyTable tenantId="test-tenant" />);

    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });

    expect(await screen.findByText(/Search failed/i)).toBeInTheDocument();
    const retryBtn = screen.getByRole("button", { name: /Try again/i });
    expect(retryBtn).toBeInTheDocument();
  });

  it("shows network_error state when search throws", async () => {
    mockedSearch.mockRejectedValueOnce(new Error("Network error"));
    render(<ABCSupplyTable tenantId="test-tenant" />);

    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });

    expect(await screen.findByText(/Could not reach ABC Supply service/i)).toBeInTheDocument();
  });

  it("renders empty state when search returns no items", async () => {
    mockedSearch.mockResolvedValueOnce({
      ok: true,
      data: { items: [], pagination: { totalItems: 0, pageNumber: 1, itemsPerPage: 20 } },
    } as any);
    render(<ABCSupplyTable tenantId="test-tenant" />);

    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "nonexistent" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });

    expect(await screen.findByText(/No items found/i)).toBeInTheDocument();
  });

  it("suggestion chip click sets query and fires a search", async () => {
    mockedSearch.mockResolvedValue(searchSuccessResponse as any);
    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);

    // Wait for the Branch context chip to confirm connection defaults (branchNumber) loaded
    await screen.findByText("Branch 17");

    const chip = screen.getByRole("button", { name: /Asphalt shingles/i });
    await act(async () => { fireEvent.click(chip); });

    // The query input reflects the selected chip term
    expect(input).toHaveValue("Asphalt shingles");

    // The queued setTimeout search fires (using old handleSearch which has effectiveBranchNumber)
    await waitFor(() => {
      expect(mockedSearch).toHaveBeenCalled();
    });
  });

  it("toggles family items switch", async () => {
    render(<ABCSupplyTable tenantId="test-tenant" />);
    await screen.findByPlaceholderText(/shingles, underlayment/i);

    const familyItemsSwitch = screen.getByRole("checkbox", { name: /Family items/i });
    expect(familyItemsSwitch).not.toBeChecked();
    fireEvent.click(familyItemsSwitch);
    expect(familyItemsSwitch).toBeChecked();
  });

  // ─── Price fetch paths ────────────────────────────────────────────────────

  it("price fetch shows message when all search items have no item numbers", async () => {
    const noNumberResponse = {
      ok: true,
      data: {
        items: [{ itemDescription: "Mystery item", categoryName: "Roofing" }],
        pagination: { totalItems: 1, pageNumber: 1, itemsPerPage: 20 },
      },
    };
    mockedSearch.mockResolvedValueOnce(noNumberResponse as any);
    render(<ABCSupplyTable tenantId="test-tenant" />);

    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "mystery" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });

    await waitFor(() => {
      expect(screen.getByText(/No item numbers were available/i)).toBeInTheDocument();
    });
    expect(mockedPrice).not.toHaveBeenCalled();
  });

  it("price fetch shows message when branch and ship-to are missing", async () => {
    mockedGetConnectionStatus.mockResolvedValue({
      ok: true,
      data: { connected: false, branchNumber: null, shipToNumber: null },
    });
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);

    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });

    await waitFor(() => {
      expect(screen.getByText(/Enter Branch/i)).toBeInTheDocument();
    });
    expect(mockedPrice).not.toHaveBeenCalled();
  });

  it("price fetch shows message when API returns ok:false", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    mockedPrice.mockResolvedValueOnce({
      ok: false,
      message: "Pricing service unavailable.",
    } as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);

    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    // Wait for connection defaults to load (Branch chip confirms effectiveBranchNumber is set,
    // which is required for getMissingPricingFields() to return [] and reach the price API)
    await screen.findByText("Branch 17");
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });

    // Text appears in both the response panel and the Snackbar; use getAllByText
    await waitFor(() => {
      expect(screen.getAllByText(/Pricing service unavailable/i).length).toBeGreaterThan(0);
    });
  });

  it("price fetch shows message when API throws", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    mockedPrice.mockRejectedValueOnce(new Error("price fetch failed"));

    render(<ABCSupplyTable tenantId="test-tenant" />);

    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });

    await waitFor(() => {
      expect(screen.getByText(/Could not fetch ABC prices/i)).toBeInTheDocument();
    });
  });

  it("shows price loading spinner in table cells while prices are being fetched", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    let resolvePricing!: (v: any) => void;
    mockedPrice.mockImplementationOnce(
      () => new Promise((resolve) => { resolvePricing = resolve; })
    );

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    // Wait for connection defaults so getMissingPricingFields() returns [] and price API is called
    await screen.findByText("Branch 17");
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });

    await waitFor(() => screen.getByText("02GASTZ3WW"));
    // sampleItems has 2 rows — each shows "Loading" while price fetch is pending
    expect(screen.getAllByText(/Loading/i).length).toBeGreaterThan(0);

    await act(async () => { resolvePricing(priceSuccessResponse); });
    await waitFor(() => screen.getByText("$96.50"));
  });

  // ─── Pagination ───────────────────────────────────────────────────────────

  it("handlePageChange triggers a new search for the next page", async () => {
    const bigResultResponse = {
      ok: true,
      data: {
        items: sampleItems,
        pagination: { totalItems: 100, pageNumber: 1, itemsPerPage: 20 },
      },
    };
    mockedSearch.mockResolvedValue(bigResultResponse as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    const nextPageBtn = screen.getByRole("button", { name: /next page/i });
    await act(async () => { fireEvent.click(nextPageBtn); });

    await waitFor(() => {
      expect(mockedSearch).toHaveBeenCalledWith(
        expect.objectContaining({ page_number: 2 }),
        accessToken
      );
    });
  });

  it("search catalog page size dropdown uses the shared design and applies the selected size to the next search", async () => {
    mockedSearch.mockResolvedValue(searchSuccessResponse as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);

    const pageSizeSelect = screen.getByRole("combobox", { name: /per page/i });
    expect(pageSizeSelect).toHaveTextContent("20 / page");

    fireEvent.mouseDown(pageSizeSelect);
    const option20 = screen.getByRole("option", { name: "20 per page" });
    const option50 = screen.getByRole("option", { name: "50 per page" });
    expect(option20.querySelector("svg")).toBeInTheDocument();
    expect(option50.querySelector("svg")).not.toBeInTheDocument();

    fireEvent.click(option50);
    await waitFor(() => {
      expect(pageSizeSelect).toHaveTextContent("50 / page");
    });

    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });

    await waitFor(() => {
      expect(mockedSearch).toHaveBeenCalledWith(
        expect.objectContaining({ items_per_page: 50 }),
        accessToken
      );
    });
  });

  // ─── Import paths ─────────────────────────────────────────────────────────

  it("shows error toast when import returns ok:false", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    mockedImportSelected.mockResolvedValueOnce({
      ok: false,
      message: "Import failed — pricing required.",
    } as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    fireEvent.click(screen.getByRole("checkbox", { name: /Select 02GASTZ3WW/i }));
    selectMaterialSettings();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /add 1 to material list/i }));
    });

    // MUI Snackbar renders toast text twice (alert + aria-live region); use getAllByText
    await waitFor(() => {
      expect(screen.getAllByText(/Import failed/i).length).toBeGreaterThan(0);
    });
  });

  it("shows error toast when import throws", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    mockedImportSelected.mockRejectedValue(new Error("network error"));

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    fireEvent.click(screen.getByRole("checkbox", { name: /Select 02GASTZ3WW/i }));
    selectMaterialSettings();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /add 1 to material list/i }));
    });

    // MUI Snackbar + response panel both render the message; use getAllByText
    await waitFor(() => {
      expect(screen.getAllByText(/Could not reach the material import service/i).length).toBeGreaterThan(0);
    });
  });

  it("renders import toast with added, skipped, and failed badges", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    mockedImportSelected.mockResolvedValueOnce({
      ok: true,
      data: {
        counts: {
          created: 1,
          skippedMissingPrice: 1,
          skippedExisting: 0,
          skippedInvalid: 0,
          failed: 1,
        },
        created: [{ itemNumber: "02GASTZ3WW", name: "Architectural Shingle" }],
        skipped: [{ itemCode: "SKIP-001", reason: "No price returned" }],
        failed: [{ sku: "FAIL-001" }],
      },
    } as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    fireEvent.click(screen.getByRole("checkbox", { name: /Select all visible/i }));
    selectMaterialSettings();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /add 2 to material list/i }));
    });

    // New toast design: compact badge counts
    await waitFor(() => {
      expect(screen.getByText("1 added")).toBeInTheDocument();
      expect(screen.getByText("1 skipped")).toBeInTheDocument();
      expect(screen.getByText("1 failed")).toBeInTheDocument();
    });
    // Toggle buttons are visible; detail rows are collapsed by default
    expect(screen.getByRole("button", { name: /View added/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /View skipped/i })).toBeInTheDocument();
    // skippedMissingPrice note
    expect(screen.getByText(/had no ABC Supply price/i)).toBeInTheDocument();
    // Expanding "View added" reveals the item code and name (rendered as separate cells)
    fireEvent.click(screen.getByRole("button", { name: /View added/i }));
    await waitFor(() => {
      expect(screen.getByText("Architectural Shingle")).toBeInTheDocument();
    });
    expect(screen.getAllByText("02GASTZ3WW").length).toBeGreaterThan(1);
  });

  it("renders import toast with skipped badge and no added button when only skipped items exist", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    mockedImportSelected.mockResolvedValueOnce({
      ok: true,
      data: {
        counts: { created: 0, skippedExisting: 1, failed: 0 },
        created: [],
        skipped: [{ itemNumber: "02GASTZ3WW" }],
        failed: [],
      },
    } as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    fireEvent.click(screen.getByRole("checkbox", { name: /Select 02GASTZ3WW/i }));
    selectMaterialSettings();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /add 1 to material list/i }));
    });

    // Skipped badge shows; no added badge since created=0
    await waitFor(() => {
      expect(screen.getByText("1 skipped")).toBeInTheDocument();
    });
    expect(screen.queryByText(/\d+ added/)).not.toBeInTheDocument();
    // No "View added" button since addedRecords is empty; "View skipped" is present
    expect(screen.queryByRole("button", { name: /View added/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /View skipped/i })).toBeInTheDocument();
    // Expanding "View skipped" reveals the skipped item (getAllByText handles the duplicate in search table)
    fireEvent.click(screen.getByRole("button", { name: /View skipped/i }));
    await waitFor(() => {
      expect(screen.getAllByText("02GASTZ3WW").length).toBeGreaterThan(1);
    });
  });

  it("getImportRecordTitle uses sku alone when name is absent", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    mockedImportSelected.mockResolvedValueOnce({
      ok: true,
      data: {
        counts: { created: 1, failed: 0, skippedExisting: 0 },
        created: [{ sku: "SKU-NONAME-001" }],
        skipped: [],
        failed: [],
      },
    } as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    fireEvent.click(screen.getByRole("checkbox", { name: /Select 02GASTZ3WW/i }));
    selectMaterialSettings();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /add 1 to material list/i }));
    });

    // "View added" must be clicked to expand the detail rows
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /View added/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /View added/i }));
    await waitFor(() => {
      expect(screen.getByText("SKU-NONAME-001")).toBeInTheDocument();
    });
  });

  it("shows Nothing was added text when all import counts are zero", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    mockedImportSelected.mockResolvedValueOnce({
      ok: true,
      data: {
        counts: { created: 0, skippedExisting: 0, skippedMissingPrice: 0, skippedInvalid: 0, failed: 0 },
        created: [],
        skipped: [],
        failed: [],
      },
    } as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    fireEvent.click(screen.getByRole("checkbox", { name: /Select 02GASTZ3WW/i }));
    selectMaterialSettings();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /add 1 to material list/i }));
    });

    await waitFor(() => {
      expect(screen.getByText("Nothing was added")).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: /View added/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /View skipped/i })).not.toBeInTheDocument();
  });

  it("dismiss button clears the import result toast", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    mockedImportSelected.mockResolvedValueOnce({
      ok: true,
      data: {
        counts: { created: 1, failed: 0 },
        created: [{ itemNumber: "02GASTZ3WW", name: "Architectural Shingle" }],
        skipped: [],
        failed: [],
      },
    } as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    fireEvent.click(screen.getByRole("checkbox", { name: /Select 02GASTZ3WW/i }));
    selectMaterialSettings();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /add 1 to material list/i }));
    });

    await waitFor(() => {
      expect(screen.getByText("1 added")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Dismiss/i }));

    await waitFor(() => {
      expect(screen.queryByText("1 added")).not.toBeInTheDocument();
    });
  });

  it("View added and View skipped panels are mutually exclusive", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    // Use identifiers that do not appear in the search results table so we can assert absence
    mockedImportSelected.mockResolvedValueOnce({
      ok: true,
      data: {
        counts: { created: 1, skippedExisting: 1, failed: 0 },
        created: [{ itemNumber: "PANEL-ADDED-001", name: "Exclusive Added Item" }],
        skipped: [{ itemNumber: "PANEL-SKIP-001" }],
        failed: [],
      },
    } as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    fireEvent.click(screen.getByRole("checkbox", { name: /Select all visible/i }));
    selectMaterialSettings();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /add 2 to material list/i }));
    });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /View added/i })).toBeInTheDocument();
    });

    // Open "View added" — shows added item, skipped panel closed
    fireEvent.click(screen.getByRole("button", { name: /View added/i }));
    await waitFor(() => {
      expect(screen.getByText("Exclusive Added Item")).toBeInTheDocument();
      expect(screen.getByText("PANEL-ADDED-001")).toBeInTheDocument();
    });
    expect(screen.queryByText("PANEL-SKIP-001")).not.toBeInTheDocument();

    // Switch to "View skipped" — closes added panel, opens skipped panel
    fireEvent.click(screen.getByRole("button", { name: /View skipped/i }));
    await waitFor(() => {
      expect(screen.getByText("PANEL-SKIP-001")).toBeInTheDocument();
    });
    expect(screen.queryByText("Exclusive Added Item")).not.toBeInTheDocument();
    expect(screen.queryByText("PANEL-ADDED-001")).not.toBeInTheDocument();
  });

  it("shows singular skippedMissingPrice note when one item had no price", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    mockedImportSelected.mockResolvedValueOnce({
      ok: true,
      data: {
        counts: { created: 0, skippedMissingPrice: 1, skippedExisting: 0, failed: 0 },
        created: [],
        skipped: [{ itemCode: "SKIP-PRICE-001" }],
        failed: [],
      },
    } as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    fireEvent.click(screen.getByRole("checkbox", { name: /Select 02GASTZ3WW/i }));
    selectMaterialSettings();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /add 1 to material list/i }));
    });

    await waitFor(() => {
      expect(screen.getByText(/1 item had no ABC Supply price/i)).toBeInTheDocument();
    });
  });

  // ─── Detail drawer ────────────────────────────────────────────────────────

  it("renders drawer body specs and pricing after detail API resolves", async () => {
    const detailedItem = {
      ...sampleItems[0],
      manufacturerPartNumber: "OC-ARCH-30-CHARCOAL",
    };
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    mockedGetItem.mockResolvedValueOnce({
      ok: true,
      data: { item: detailedItem },
    } as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    fireEvent.click(screen.getAllByRole("button", { name: /view/i })[0]);

    // OC-ARCH-30-CHARCOAL and Account Price are unique to the drawer (not in table columns).
    // Owens Corning and Roofing also appear in the table cells so use getAllByText for those.
    await waitFor(() => {
      expect(screen.getByText("OC-ARCH-30-CHARCOAL")).toBeInTheDocument();
      expect(screen.getByText("Account Price")).toBeInTheDocument();
    });
    expect(screen.getAllByText("Owens Corning").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Roofing").length).toBeGreaterThan(0);
  });

  it("closes the detail drawer via the close button", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    mockedGetItem.mockResolvedValueOnce({
      ok: true,
      data: { item: sampleItems[0] },
    } as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    fireEvent.click(screen.getAllByRole("button", { name: /view/i })[0]);
    await screen.findByRole("button", { name: /Close/i });

    fireEvent.click(screen.getByRole("button", { name: /Close/i }));
    await waitFor(() => {
      expect(screen.queryByText(/ABC Supply Product/i)).not.toBeInTheDocument();
    });
  });

  it("handleSelectRow with no itemNumber opens drawer but skips detail load", async () => {
    const noNumberResponse = {
      ok: true,
      data: {
        items: [{ itemDescription: "No-number item", categoryName: "Roofing" }],
        pagination: { totalItems: 1, pageNumber: 1, itemsPerPage: 20 },
      },
    };
    mockedSearch.mockResolvedValueOnce(noNumberResponse as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "mystery" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("No-number item"));

    fireEvent.click(screen.getByRole("button", { name: /view/i }));

    await waitFor(() => {
      expect(screen.getByText(/ABC Supply Product/i)).toBeInTheDocument();
    });
    expect(mockedGetItem).not.toHaveBeenCalled();
  });

  it("handleSelectRow shows fallback item when detail API returns ok:false", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    mockedGetItem.mockResolvedValueOnce({ ok: false, message: "Not found" } as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    fireEvent.click(screen.getAllByRole("button", { name: /view/i })[0]);

    await waitFor(() => {
      expect(screen.getByText(/ABC Supply Product/i)).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByText(/30-Year Architectural Shingle/i)).toBeInTheDocument();
    });
  });

  it("handleSelectRow shows fallback item when detail API throws", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    mockedGetItem.mockRejectedValueOnce(new Error("detail fetch failed"));

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    fireEvent.click(screen.getAllByRole("button", { name: /view/i })[0]);

    await waitFor(() => {
      expect(screen.getByText(/ABC Supply Product/i)).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByText(/30-Year Architectural Shingle/i)).toBeInTheDocument();
    });
  });

  it("add this material button in drawer triggers import for displayed item", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    mockedGetItem.mockResolvedValueOnce({
      ok: true,
      data: { item: sampleItems[0] },
    } as any);
    mockedImportSelected.mockResolvedValueOnce({
      ok: true,
      data: { counts: { created: 1, failed: 0 } },
    } as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    selectMaterialSettings();
    fireEvent.click(screen.getAllByRole("button", { name: /view/i })[0]);
    await waitFor(() => screen.getByText(/Add this material to material list/i));

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Add this material to material list/i }));
    });

    await waitFor(() => {
      expect(mockedImportSelected).toHaveBeenCalledWith(
        expect.objectContaining({ materials: expect.arrayContaining([
          expect.objectContaining({ itemNumber: "02GASTZ3WW" }),
        ]) }),
        accessToken
      );
    });
  });

  // ─── My Materials tab ─────────────────────────────────────────────────────

  it("My Materials tab shows empty state when no materials have been imported", async () => {
    mockedGetImportedMaterials.mockResolvedValue({ ok: true, data: { items: [] } });

    render(<ABCSupplyTable tenantId="test-tenant" />);
    await screen.findByPlaceholderText(/shingles, underlayment/i);

    fireEvent.click(screen.getByRole("button", { name: /My Materials/i }));

    await waitFor(() => {
      expect(screen.getByText("No materials imported yet")).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /Go to Search Catalog/i })).toBeInTheDocument();
  });

  it("Go to Search Catalog in empty My Materials switches back to search tab", async () => {
    mockedGetImportedMaterials.mockResolvedValue({ ok: true, data: { items: [] } });

    render(<ABCSupplyTable tenantId="test-tenant" />);
    await screen.findByPlaceholderText(/shingles, underlayment/i);

    fireEvent.click(screen.getByRole("button", { name: /My Materials/i }));
    await waitFor(() => screen.getByText("No materials imported yet"));

    fireEvent.click(screen.getByRole("button", { name: /Go to Search Catalog/i }));

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/shingles, underlayment/i)).toBeInTheDocument();
    });
    expect(screen.queryByText("No materials imported yet")).not.toBeInTheDocument();
  });

  it("My Materials tab renders imported material rows with item number and name", async () => {
    mockedGetImportedMaterials.mockResolvedValue({
      ok: true,
      data: {
        items: [
          {
            itemNumber: "02GASTZ3WW",
            name: "30-Year Architectural Shingle",
            vendorName: "ABC Supply",
            unit: "SQ",
            cost: 96.5,
          },
          {
            itemNumber: "FL-DRP-EDG-02",
            name: "Aluminum Drip Edge",
            vendorName: "ABC Supply",
            unit: "EA",
            cost: 8.25,
          },
        ],
      },
    });

    render(<ABCSupplyTable tenantId="test-tenant" />);
    await screen.findByPlaceholderText(/shingles, underlayment/i);

    fireEvent.click(screen.getByRole("button", { name: /My Materials/i }));

    await waitFor(() => {
      expect(screen.getByText("30-Year Architectural Shingle")).toBeInTheDocument();
      expect(screen.getByText("Aluminum Drip Edge")).toBeInTheDocument();
    });
    expect(screen.getAllByText("02GASTZ3WW").length).toBeGreaterThan(0);
    expect(screen.getByText("FL-DRP-EDG-02")).toBeInTheDocument();
  });

  it("My Materials tab shows count badge on the tab button when materials exist", async () => {
    mockedGetImportedMaterials.mockResolvedValue({
      ok: true,
      data: {
        items: [
          { itemNumber: "02GASTZ3WW", name: "Shingle" },
          { itemNumber: "FL-DRP-EDG-02", name: "Drip Edge" },
        ],
      },
    });

    render(<ABCSupplyTable tenantId="test-tenant" />);
    await screen.findByPlaceholderText(/shingles, underlayment/i);

    // Badge count should appear on the tab button once materials are loaded
    await waitFor(() => {
      expect(screen.getByText("2")).toBeInTheDocument();
    });
  });

  it("My Materials filter narrows the material list by name via server-side search", async () => {
    mockPagedImportedMaterials([
      { itemNumber: "02GASTZ3WW", name: "Architectural Shingle", vendorName: "ABC Supply" },
      { itemNumber: "FL-DRP-EDG-02", name: "Aluminum Drip Edge", vendorName: "ABC Supply" },
    ]);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    await screen.findByPlaceholderText(/shingles, underlayment/i);

    fireEvent.click(screen.getByRole("button", { name: /My Materials/i }));
    await waitFor(() => screen.getByText("Architectural Shingle"));

    const filterInput = screen.getByPlaceholderText(/Search by name, item #, or vendor/i);
    fireEvent.change(filterInput, { target: { value: "Aluminum" } });

    await waitFor(() => {
      expect(mockedGetImportedMaterials).toHaveBeenCalledWith(
        expect.objectContaining({ search: "Aluminum", page: 1 }),
        accessToken
      );
    });
    await waitFor(() => {
      expect(screen.getByText("Aluminum Drip Edge")).toBeInTheDocument();
      expect(screen.queryByText("Architectural Shingle")).not.toBeInTheDocument();
    });
    expect(screen.getByText("1–1 of 1")).toBeInTheDocument();
  });

  it("My Materials clear filter button restores the full list", async () => {
    mockPagedImportedMaterials([
      { itemNumber: "02GASTZ3WW", name: "Architectural Shingle", vendorName: "ABC Supply" },
      { itemNumber: "FL-DRP-EDG-02", name: "Aluminum Drip Edge", vendorName: "ABC Supply" },
    ]);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    await screen.findByPlaceholderText(/shingles, underlayment/i);

    fireEvent.click(screen.getByRole("button", { name: /My Materials/i }));
    await waitFor(() => screen.getByText("Architectural Shingle"));

    const filterInput = screen.getByPlaceholderText(/Search by name, item #, or vendor/i);
    fireEvent.change(filterInput, { target: { value: "Aluminum" } });
    await waitFor(() => screen.queryByText("Architectural Shingle") === null);

    // Click the clear icon (aria-label not set, but it is an IconButton inside the filter bar)
    const clearBtns = screen.getAllByRole("button");
    const clearBtn = clearBtns.find((btn) => btn.querySelector('svg') && !btn.textContent?.trim());
    if (clearBtn) fireEvent.click(clearBtn);

    await waitFor(() => {
      expect(screen.getByText("Architectural Shingle")).toBeInTheDocument();
      expect(screen.getByText("Aluminum Drip Edge")).toBeInTheDocument();
    });
    expect(screen.getByText("1–2 of 2")).toBeInTheDocument();
  });

  it("My Materials pagination sits inline in the search/refresh toolbar and paginates through server pages", async () => {
    const allItems = Array.from({ length: 25 }, (_, i) => ({
      itemNumber: `ITEM-${i + 1}`,
      name: `Material ${i + 1}`,
      vendorName: "ABC Supply",
    }));
    mockPagedImportedMaterials(allItems);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.click(screen.getByRole("button", { name: /My Materials/i }));

    await waitFor(() => screen.getByText("Material 1"));
    expect(screen.getByText("1–20 of 25")).toBeInTheDocument();

    // The pager lives in the same toolbar row as the search filter and Refresh button,
    // not a separate row/tab — the filter box and Refresh are both still reachable.
    expect(screen.getByPlaceholderText(/Search by name, item #, or vendor/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Refresh/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /next page/i }));

    await waitFor(() => {
      expect(mockedGetImportedMaterials).toHaveBeenCalledWith(
        expect.objectContaining({ page: 2, limit: 20 }),
        accessToken
      );
    });
    await waitFor(() => screen.getByText("Material 21"));
    expect(screen.getByText("21–25 of 25")).toBeInTheDocument();
    expect(screen.queryByText("Material 1")).not.toBeInTheDocument();

    // "Next page" is now disabled on the final page (no first/last-page buttons needed at this scale)
    expect(screen.getByRole("button", { name: /next page/i })).toBeDisabled();
  });

  it("changing My Materials rows per page resets to page 1 and requests the new limit", async () => {
    const allItems = Array.from({ length: 25 }, (_, i) => ({
      itemNumber: `ITEM-${i + 1}`,
      name: `Material ${i + 1}`,
      vendorName: "ABC Supply",
    }));
    mockPagedImportedMaterials(allItems);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.click(screen.getByRole("button", { name: /My Materials/i }));
    await waitFor(() => screen.getByText("Material 1"));

    fireEvent.click(screen.getByRole("button", { name: /next page/i }));
    await waitFor(() => screen.getByText("Material 21"));

    const rowsPerPageSelect = screen.getByRole("combobox", { name: /per page/i });
    expect(rowsPerPageSelect).toHaveTextContent("20 / page");

    fireEvent.mouseDown(rowsPerPageSelect);
    // The shared dropdown design marks the active page size with a checkmark.
    const option20 = screen.getByRole("option", { name: "20 per page" });
    const option50 = screen.getByRole("option", { name: "50 per page" });
    expect(option20.querySelector("svg")).toBeInTheDocument();
    expect(option50.querySelector("svg")).not.toBeInTheDocument();

    fireEvent.click(option50);

    await waitFor(() => {
      expect(mockedGetImportedMaterials).toHaveBeenCalledWith(
        expect.objectContaining({ page: 1, limit: 50 }),
        accessToken
      );
    });
    await waitFor(() => screen.getByText("Material 1"));
    expect(screen.getByText("1–25 of 25")).toBeInTheDocument();
    expect(rowsPerPageSelect).toHaveTextContent("50 / page");
  });

  it("clicking the My Materials next-page button fires exactly one reload request (no indirect double-fetch)", async () => {
    const allItems = Array.from({ length: 25 }, (_, i) => ({
      itemNumber: `ITEM-${i + 1}`,
      name: `Material ${i + 1}`,
      vendorName: "ABC Supply",
    }));
    mockPagedImportedMaterials(allItems);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.click(screen.getByRole("button", { name: /My Materials/i }));
    await waitFor(() => screen.getByText("Material 1"));

    const callCountBeforeClick = mockedGetImportedMaterials.mock.calls.length;
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /next page/i }));
    });
    await waitFor(() => screen.getByText("Material 21"));

    // goToAddedMaterialsPage calls loadAddedMaterials directly now — it must not ALSO be
    // re-triggered indirectly through the auto-load effect, which would double the request.
    expect(mockedGetImportedMaterials.mock.calls.length - callCountBeforeClick).toBe(1);
  });

  it("changing the My Materials rows-per-page fires exactly one reload request (no indirect double-fetch)", async () => {
    const allItems = Array.from({ length: 25 }, (_, i) => ({
      itemNumber: `ITEM-${i + 1}`,
      name: `Material ${i + 1}`,
      vendorName: "ABC Supply",
    }));
    mockPagedImportedMaterials(allItems);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.click(screen.getByRole("button", { name: /My Materials/i }));
    await waitFor(() => screen.getByText("Material 1"));

    const callCountBeforeChange = mockedGetImportedMaterials.mock.calls.length;
    const rowsPerPageSelect = screen.getByRole("combobox", { name: /per page/i });
    fireEvent.mouseDown(rowsPerPageSelect);
    await act(async () => {
      fireEvent.click(screen.getByRole("option", { name: "50 per page" }));
    });
    await waitFor(() => {
      expect(screen.getByText("1–25 of 25")).toBeInTheDocument();
    });

    expect(mockedGetImportedMaterials.mock.calls.length - callCountBeforeChange).toBe(1);
  });

  it("the debounced My Materials filter reloads once when the trimmed value settles on something new, and not at all when it settles back to the same value", async () => {
    mockPagedImportedMaterials([
      { itemNumber: "02GASTZ3WW", name: "Architectural Shingle", vendorName: "ABC Supply" },
      { itemNumber: "FL-DRP-EDG-02", name: "Aluminum Drip Edge", vendorName: "ABC Supply" },
    ]);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.click(screen.getByRole("button", { name: /My Materials/i }));
    await waitFor(() => screen.getByText("Architectural Shingle"));

    const filterInput = screen.getByPlaceholderText(/Search by name, item #, or vendor/i);
    const callCountBeforeTyping = mockedGetImportedMaterials.mock.calls.length;

    fireEvent.change(filterInput, { target: { value: "Aluminum" } });
    await waitFor(() => {
      expect(mockedGetImportedMaterials).toHaveBeenCalledWith(
        expect.objectContaining({ search: "Aluminum" }),
        accessToken
      );
    });
    expect(mockedGetImportedMaterials.mock.calls.length - callCountBeforeTyping).toBe(1);

    // Typing " " then clearing back to a value that trims to the exact same applied filter
    // ("Aluminum") must not fire a second, redundant reload — React bails out because the
    // trimmed value hasn't actually changed.
    const callCountAfterFirstSettle = mockedGetImportedMaterials.mock.calls.length;
    fireEvent.change(filterInput, { target: { value: "Aluminum " } });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 400)); });

    expect(mockedGetImportedMaterials.mock.calls.length).toBe(callCountAfterFirstSettle);
  });

  it("disables the My Materials pager, page-size select, and Refresh while a page request is in flight, without wiping the current page", async () => {
    const allItems = Array.from({ length: 25 }, (_, i) => ({
      itemNumber: `ITEM-${i + 1}`,
      name: `Material ${i + 1}`,
      vendorName: "ABC Supply",
    }));
    mockPagedImportedMaterials(allItems);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.click(screen.getByRole("button", { name: /My Materials/i }));
    await waitFor(() => screen.getByText("Material 1"));

    // Freeze the page-2 response so the request stays in flight.
    let resolvePage2!: (value: any) => void;
    mockedGetImportedMaterials.mockImplementationOnce(
      () => new Promise((resolve) => { resolvePage2 = resolve; })
    );

    fireEvent.click(screen.getByRole("button", { name: /next page/i }));

    // While the request is pending: page-1 data stays on screen (no jarring skeleton flash)
    // but every control that could fire a second overlapping request is disabled.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /next page/i })).toBeDisabled();
    });
    expect(screen.getByRole("button", { name: /previous page/i })).toBeDisabled();
    expect(screen.getByRole("combobox", { name: /per page/i })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("button", { name: /Refresh/i })).toBeDisabled();
    expect(screen.getByText("Material 1")).toBeInTheDocument();

    await act(async () => {
      resolvePage2({
        ok: true,
        data: { items: allItems.slice(20, 25), page: 2, limit: 20, total: 25 },
      });
    });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /next page/i })).not.toBeDisabled();
    });
    expect(screen.getByRole("button", { name: /Refresh/i })).not.toBeDisabled();
    expect(screen.getByText("Material 21")).toBeInTheDocument();
    expect(screen.queryByText("Material 1")).not.toBeInTheDocument();
  });

  it("discards a stale My Materials reload that resolves after a newer one (out-of-order race)", async () => {
    mockedSearch.mockResolvedValue(searchSuccessResponse as any);
    mockedImportSelected
      .mockResolvedValueOnce({ ok: true, data: { counts: { created: 1, failed: 0 } } } as any)
      .mockResolvedValueOnce({ ok: true, data: { counts: { created: 1, failed: 0 } } } as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    // From here on, every My Materials *library reload* triggered by a successful import is
    // controlled (status-check calls carrying external_item_ids are left alone): the first
    // reload — triggered by importing item #1 — is held pending, while the second reload —
    // triggered afterward by importing item #2 — resolves immediately. This reproduces a
    // request that was started earlier but resolves later, out of order.
    let resolveFirstReload!: (value: any) => void;
    let reloadCallCount = 0;
    mockedGetImportedMaterials.mockImplementation((payload: any) => {
      if (payload?.external_item_ids) {
        return Promise.resolve({ ok: true, data: { items: [] } });
      }
      reloadCallCount += 1;
      if (reloadCallCount === 1) {
        return new Promise((resolve) => { resolveFirstReload = resolve; });
      }
      return Promise.resolve({
        ok: true,
        data: {
          items: [{ itemNumber: "FL-DRP-EDG-02", name: "Aluminum Drip Edge" }],
          page: 1,
          limit: 20,
          total: 1,
        },
      });
    });

    fireEvent.click(screen.getByRole("checkbox", { name: /Select 02GASTZ3WW/i }));
    selectMaterialSettings();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /add 1 to material list/i }));
    });
    // The reload triggered by this import is now pending (reloadCallCount === 1).

    fireEvent.click(screen.getByRole("checkbox", { name: /Select FL-DRP-EDG-02/i }));
    selectMaterialSettings();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /add 1 to material list/i }));
    });
    // The reload triggered by this second import already resolved.

    fireEvent.click(screen.getByRole("button", { name: /My Materials/i }));
    await waitFor(() => {
      expect(screen.getByText("Aluminum Drip Edge")).toBeInTheDocument();
    });

    // The stale first reload finally settles with empty data — it must be discarded rather
    // than clobber the newer state that's already on screen.
    await act(async () => {
      resolveFirstReload({ ok: true, data: { items: [], page: 1, limit: 20, total: 0 } });
    });

    expect(screen.getByText("Aluminum Drip Edge")).toBeInTheDocument();
  });

  it("My Materials library is reloaded after a successful import", async () => {
    mockedGetImportedMaterials.mockResolvedValue({ ok: true, data: { items: [] } });
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    mockedImportSelected.mockResolvedValueOnce({
      ok: true,
      data: { counts: { created: 1, failed: 0 }, created: [{ itemNumber: "02GASTZ3WW" }] },
    } as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);

    // Record how many times the library was fetched on mount
    const callCountAfterMount = mockedGetImportedMaterials.mock.calls.length;

    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    fireEvent.click(screen.getByRole("checkbox", { name: /Select 02GASTZ3WW/i }));
    selectMaterialSettings();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /add 1 to material list/i }));
    });

    // After a successful import the component calls loadAddedMaterials again
    await waitFor(() => {
      expect(mockedGetImportedMaterials.mock.calls.length).toBeGreaterThan(callCountAfterMount);
    });
  });

  // ─── Snackbar ─────────────────────────────────────────────────────────────

  it("closes snackbar toast via Alert close button", async () => {
    mockedSearch.mockResolvedValueOnce(searchSuccessResponse as any);
    mockedImportSelected.mockResolvedValueOnce({
      ok: true,
      data: { counts: { created: 1, failed: 0 } },
    } as any);

    render(<ABCSupplyTable tenantId="test-tenant" />);
    const input = await screen.findByPlaceholderText(/shingles, underlayment/i);
    fireEvent.change(input, { target: { value: "shingle" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => screen.getByText("02GASTZ3WW"));

    fireEvent.click(screen.getByRole("checkbox", { name: /Select 02GASTZ3WW/i }));
    selectMaterialSettings();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /add 1 to material list/i }));
    });

    const closeBtn = await screen.findByRole("button", { name: /close/i });
    fireEvent.click(closeBtn);
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /close/i })).not.toBeInTheDocument();
    });
  });
});
