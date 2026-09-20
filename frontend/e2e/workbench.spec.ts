import { expect, test } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const fixtures = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
  "fixtures",
);

test("review, persist, export, and reuse only unchanged evidence", async ({
  page,
  request,
}) => {
  const consoleErrors: string[] = [];
  page.on("pageerror", (error) => consoleErrors.push(error.message));
  const seeded = await (
    await request.post("/api/demo", { data: { variant: "opening" } })
  ).json();
  // Each test-server invocation starts with a fresh seeded database.
  await page.goto("/");
  await page.getByTestId("run-selector").selectOption(seeded.id);
  await expect(page.getByRole("heading", { name: /Every game/ })).toBeVisible();
  await expect(page.getByText("Synthetic demo", { exact: true })).toBeVisible();

  let run = await (await request.get(`/api/runs/${seeded.id}`)).json();
  expect(run.open_cases).toBe(8);
  await expect(page.getByTestId("export-button")).toBeDisabled();
  await page.getByRole("button", { name: /^Review queue/ }).click();
  const item = run.cases.find(
    (c: any) => c.status === "open" && c.kind === "conflict",
  );
  expect(item).toBeDefined();
  {
    await page.getByTestId(`review-case-${item.id}`).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByTestId("apply-decision")).toBeDisabled();
    const candidate = run.candidates.find(
      (c: any) =>
        item.candidate_ids.includes(c.id) && c.valid && c.source === "league",
    );
    await page.getByTestId(`candidate-${candidate.id}`).check();
    await page.getByTestId("reviewer-name").fill("Browser reviewer");
    await page
      .getByTestId("decision-note")
      .fill("Compared the source score against the sample review evidence.");
    await expect(page.getByTestId("apply-decision")).toBeDisabled();
    await page.getByTestId("preview-button").click();
    await expect(
      page.getByRole("region", { name: "Decision preview" }),
    ).toBeVisible();
    await expect(page.getByTestId("apply-decision")).toBeEnabled();
    await page.getByTestId("apply-decision").click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.reload();
    run = await (await request.get(`/api/runs/${seeded.id}`)).json();
    expect(
      run.cases.find((c: any) => c.id === item.id).resolution.candidate_id,
    ).toBe(candidate.id);
  }

  // Exercise the disabled invalid-candidate control, not just API validation.
  await page.getByRole("button", { name: /^Review queue/ }).click();
  const invalidCase = run.cases.find(
    (c: any) => c.status === "open" && c.kind === "invalid",
  );
  expect(invalidCase).toBeDefined();
  {
    await page.getByTestId(`review-case-${invalidCase.id}`).click();
    const invalid = run.candidates.find(
      (c: any) => invalidCase.candidate_ids.includes(c.id) && !c.valid,
    );
    await expect(page.getByTestId(`candidate-${invalid.id}`)).toBeDisabled();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
  }

  // API tests cover every rule/transaction. Finish remaining cases here so this
  // browser test can verify the enabled download and cross-release UX promptly.
  for (const pending of run.cases.filter((c: any) => c.status === "open")) {
    const candidate = run.candidates.find(
      (c: any) => pending.candidate_ids.includes(c.id) && c.valid,
    );
    const response = await request.post(
      `/api/runs/${run.id}/cases/${pending.id}/resolve`,
      {
        data: {
          action: candidate ? "select" : "exclude",
          candidate_id: candidate?.id,
          expected_version: pending.version,
          reviewer: "Browser reviewer",
          note: "Reviewed the complete synthetic source evidence for this case.",
        },
      },
    );
    expect(response.ok()).toBeTruthy();
  }
  await page.getByRole("button", { name: "Refresh current run" }).click();
  await expect(page.getByTestId("export-button")).toBeEnabled();
  const downloaded = page.waitForEvent("download");
  await page.getByTestId("export-button").click();
  const bundle = await downloaded;
  expect(bundle.suggestedFilename()).toMatch(/\.zip$/);
  expect(await bundle.failure()).toBeNull();
  const downloadPath = await bundle.path();
  expect(downloadPath).not.toBeNull();
  expect((await fs.readFile(downloadPath!)).subarray(0, 2).toString()).toBe(
    "PK",
  );

  await page.getByRole("button", { name: "Load follow-up demo" }).click();
  await expect(page.getByTestId("replay-button")).toBeEnabled();
  await page.getByTestId("replay-button").click();
  await page
    .getByRole("dialog")
    .getByLabel("Reviewer name")
    .fill("Browser reviewer");
  await page.getByRole("button", { name: "Reuse matching decisions" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("status")).toContainText("7 decisions reused");
  const followupId = await page.getByTestId("run-selector").inputValue();
  const followup = await (await request.get(`/api/runs/${followupId}`)).json();
  expect(followup.open_cases).toBe(1);
  expect(followup.resolved_cases).toBe(7);
  await expect(page.getByTestId("export-button")).toBeDisabled();
  await page.getByRole("button", { name: "Audit trail", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Audit trail", exact: true }),
  ).toBeVisible();
  expect(consoleErrors).toEqual([]);
});

test("imports two real files and rejects malformed CSV without losing current data", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByTestId("import-button").click();
  await page
    .getByLabel("Run name", { exact: true })
    .fill("Browser clean-file import");
  await page
    .getByLabel("Scorer export", { exact: true })
    .setInputFiles(path.join(fixtures, "clean_scorer.csv"));
  await page
    .getByLabel("League export", { exact: true })
    .setInputFiles(path.join(fixtures, "clean_league.csv"));
  await page
    .getByRole("button", { name: "Reconcile files", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByTestId("export-button")).toBeEnabled();
  const current = await page.getByTestId("run-selector").inputValue();
  await page.getByTestId("import-button").click();
  await page.getByLabel("Run name", { exact: true }).fill("Malformed import");
  await page.getByLabel("Scorer export", { exact: true }).setInputFiles({
    name: "bad.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("wrong,headers\n1,2\n"),
  });
  await page
    .getByLabel("League export", { exact: true })
    .setInputFiles(path.join(fixtures, "clean_league.csv"));
  await page
    .getByRole("button", { name: "Reconcile files", exact: true })
    .click();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
    "missing required headers",
  );
  await page.keyboard.press("Escape");
  expect(await page.getByTestId("run-selector").inputValue()).toBe(current);
});

test("mobile navigation, keyboard dialog dismissal, and horizontal containment", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Every game/ })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth + 1,
    ),
  ).toBeFalsy();
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page
    .getByRole("button", { name: "Source lineage", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Source lineage", exact: true }),
  ).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth + 1,
  );
  expect(overflow).toBeFalsy();
  await page.getByTestId("import-button").click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByTestId("import-button")).toBeFocused();
});
