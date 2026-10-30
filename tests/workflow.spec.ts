import { test, expect } from "@playwright/test";

test("incident → plan → approval → refusal → recalculation → passenger page", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: "Resetează scenariul" }).click();
  await page.getByRole("button", { name: "Resetează demo-ul" }).click();
  await expect(
    page.getByRole("heading", { name: "Panou de incident" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Incident nou" }).click();
  await page
    .getByRole("button", { name: "Deschide incidentul", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(page.locator(".view-content")).toHaveCSS("opacity", "1");
  await page.screenshot({
    path: "test-results/overview-desktop.png",
    fullPage: true,
    animations: "disabled",
  });
  await page
    .getByRole("button", { name: "Generează planul", exact: true })
    .first()
    .click();
  await page.getByRole("button", { name: "Verifică & aprobă" }).click();
  await page.getByRole("button", { name: /Aprobă 43/ }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await page.getByRole("button", { name: "Simulează răspunsuri" }).click();
  await expect(
    page.getByText("Un furnizor nu mai poate asigura serviciile"),
  ).toBeVisible();
  await page.getByRole("button", { name: "Recalculează", exact: true }).click();
  await page.getByRole("button", { name: "Verifică & aprobă" }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: /Aprobă .* RON/ })
    .click();
  await page.getByRole("link", { name: "Furnizori", exact: true }).click();
  await page
    .getByRole("button", { name: "Confirmă solicitările", exact: true })
    .click();
  await page.getByRole("link", { name: "Pasageri", exact: true }).click();
  await page
    .getByRole("button", { name: "Pagina pasagerului G001", exact: true })
    .click();
  await expect(page.getByText("Serviciile tale sunt confirmate")).toBeVisible();
  await expect(page.getByText("DEMO-G001-MASA")).toBeVisible();
  await page.screenshot({
    path: "test-results/passenger-preview.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("button", { name: "Închide dialogul" }).click();
  await page
    .getByRole("link", { name: "Istoric acțiuni", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Refuz furnizor" }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("responsive overview and comparison are usable", async ({ page }) => {
  await page.goto("/");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("heading", { name: "Panou de incident" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await expect(page.locator(".view-content")).toHaveCSS("opacity", "1");
  await page.screenshot({
    path: "test-results/overview-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("button", { name: "Compară cu regula simplă" }).click();
  await expect(
    page.getByText("Rezultate simulate, fără garanție de optim global"),
  ).toBeVisible();
  await expect(
    page.getByText("Confirmări simulate*", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Închide dialogul" }).click();
});

test("CSV import and manual adjustment work from the console", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Incident nou" }).click();
  await page.getByLabel("Număr pasageri").fill("4");
  await page
    .getByLabel("Încarcă scenariul fictiv de 186 de pasageri")
    .uncheck();
  await page
    .getByRole("button", { name: "Deschide incidentul", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await page.getByRole("link", { name: "Pasageri", exact: true }).click();
  await page.getByRole("button", { name: "Import CSV", exact: true }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "pasageri.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      "passenger_id,name,group_id,room_capacity,accessible,bags,eligible\nP1,Ana,F1,2,false,1,true\nP2,Dan,F1,2,false,1,true\nP3,Mara,F1,2,false,1,true\nP4,Ion,F1,2,false,1,true",
    ),
  });
  await expect(page.getByRole("dialog")).toBeHidden();
  await page
    .getByRole("button", { name: "Generează planul", exact: true })
    .click();
  await page.getByRole("button", { name: "Ajustează F1", exact: true }).click();
  await page.getByLabel("Hotel dorit").selectOption("h1");
  await page
    .getByLabel("Motivul modificării")
    .fill("Preferința familiei pentru apropierea de aeroport.");
  await page
    .getByRole("button", { name: "Verifică și aplică mutarea" })
    .click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(page.locator("tbody tr").first()).toContainText(
    "Aeroport Garden",
  );
  await page
    .getByRole("link", { name: "Plan de alocare", exact: true })
    .click();
  await expect(page.getByText(/Ajustare manuală pentru F1/)).toBeVisible();
  await page
    .getByRole("link", { name: "Panou de incident", exact: true })
    .click();
  await page.getByRole("button", { name: "Resetează scenariul" }).click();
  await page.getByRole("button", { name: "Resetează demo-ul" }).click();
});

test("command search, resource tabs, theme, sidebar and pagination", async ({
  page,
  request,
}) => {
  const current = await (await request.get("/api/workspace")).json();
  await request.post("/api/reset", { data: { revision: current.revision } });
  await page.goto("/");
  await page.getByRole("tab", { name: /Transferuri/ }).click();
  await expect(page.getByRole("tabpanel")).toContainText("AeroTransfer");
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Rețea" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByRole("tabpanel")).toContainText(
    "Punctul de întâlnire B",
  );
  await page.getByRole("tab", { name: /Cazare/ }).click();
  await page.getByRole("button", { name: "Activează tema întunecată" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator(".view-content")).toHaveCSS("opacity", "1");
  await page.screenshot({
    path: "test-results/overview-dark.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("button", { name: "Restrânge bara laterală" }).click();
  await expect(page.locator(".app-shell")).toHaveClass(/sidebar-collapsed/);
  await page.keyboard.press("Control+k");
  const search = page.getByRole("combobox");
  await expect(search).toBeFocused();
  await search.fill("G080");
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("heading", { name: "Pasageri", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Caută pasager sau grup" }),
  ).toHaveValue("G080");
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await page.getByRole("link", { name: "Pasageri", exact: true }).click();
  await expect(page.locator("tbody tr")).toHaveCount(20);
  await page.getByRole("button", { name: "Pagina următoare" }).click();
  await expect(page.getByText("21–40 din 88 grupuri")).toBeVisible();
  await page
    .getByRole("textbox", { name: "Caută pasager sau grup" })
    .fill("pasager-inexistent");
  await expect(
    page.getByText("Niciun pasager nu corespunde căutării."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Activează tema luminoasă" }).click();
  await page.getByRole("button", { name: "Extinde bara laterală" }).click();
});

test("mobile passenger itinerary and mobile command search", async ({
  page,
  request,
}) => {
  let s = await (await request.get("/api/workspace")).json();
  for (const path of [
    "/reset",
    "/plan/generate",
    "/plan/approve",
    "/demo/confirm-all",
  ]) {
    const response = await request.post(`/api${path}`, {
      data: { revision: s.revision },
    });
    expect(response.ok()).toBe(true);
    s = await response.json();
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/p/${s.groups[0].token}`);
  await expect(page.getByText("Serviciile tale sunt confirmate")).toBeVisible();
  await expect(page.getByText("DEMO-G001-MASA")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/passenger-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Caută în StrandedOS" }).click();
  await page.getByRole("combobox").fill("tema întunecată");
  await page.getByRole("option").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "Caută în StrandedOS" }).click();
  await page.getByRole("button", { name: "Închide căutarea" }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await request.post("/api/reset", { data: { revision: s.revision } });
});
