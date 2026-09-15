import { expect, test, type Page } from "@playwright/test";
import messages from "../src/i18n/messages/en.json";
import {
  API_BASE,
  PASSWORD,
  PRIMARY_EMAIL,
  assertNoSeriousViolations,
  emailsSoFar,
  login,
  setTheme,
  tokenFrom,
  waitForHydration,
  waitForEmail,
} from "./support/fixtures";

/**
 * The six P1 endpoints that shipped without screens, driven the way a person would.
 *
 * Each of these had an entry in the rule-14 register — an endpoint the app serves that nothing
 * under `frontend/src` calls. That is the failure the register exists to name: a capability the
 * product does not have, which passes every test and appears in the OpenAPI schema.
 *
 * **Nothing here is seeded around.** The invitation flow runs invite → mail → accept → sign in,
 * end to end, because the whole reason `POST /invitations/accept` went unnoticed is that no
 * test had ever reached it through a screen. `seed_e2e.py` deliberately bypasses the flow — see
 * `_ensure_cross_company_membership`, which exists precisely because accepting needs a mailed
 * token — so a seeded shortcut here would reproduce the blind spot rather than close it.
 *
 * The tokens come from the mail catcher (`waitForEmail`), which is how the backend's own tests
 * read them too. No endpoint returns a token to the browser.
 */

const auth = messages.auth;
const maintenance = messages.maintenance;

/** A fresh address per run, so a re-run never collides with a membership left by the last. */
function uniqueEmail(prefix: string): string {
  return `${prefix}.${Date.now()}.${Math.floor(Math.random() * 10_000)}@vinea.example`;
}

async function inviteFromTheUsersScreen(page: Page, email: string): Promise<void> {
  await page.goto("/administration/users");
  await page.getByRole("button", { name: maintenance.inviteUser }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel(maintenance.email).fill(email);
  // Give the invitation a role. Not required by the form, but an invited user with none can
  // sign in and see nothing, which is not what this flow is demonstrating.
  const firstRole = dialog.getByRole("checkbox").first();
  if (await firstRole.isVisible().catch(() => false)) await firstRole.check();
  await dialog.getByRole("button", { name: maintenance.sendInvitation }).click();
  await expect(page.getByText(maintenance.invitationSent).first()).toBeVisible();
}

test.describe("P1 gap — the auth screens", () => {
  // These flows cross two browser contexts — an invite, a mail round trip, an accept, then a
  // sign-in — so they need more than the 45s a single-screen spec gets.
  test.describe.configure({ timeout: 120_000 });

  /**
   * Compile these four routes before the first test needs them.
   *
   * `next dev` compiles a route on its first request, and CI's warm-up loop is generated from
   * `nav-tree.ts` — which these are deliberately not in, because they are the signed-out
   * screens. So the first test to open one paid the whole compile inside its own budget: on a
   * container fresh from `make db-reset` that was **over two minutes**, against 7s warm. It
   * would have looked like a hung flow rather than a cold cache.
   *
   * Plain requests, not a browser: compiling is what is wanted, and nothing here needs a page,
   * and an unauthenticated request still makes the dev server build the route.
   */
  test.beforeAll(async ({ request }) => {
    for (const route of [
      "/login",
      "/forgot-password",
      "/reset-password",
      "/verify-email",
      "/invitations/accept",
      // The shell routes this spec drives as well. `/` is the heavy one — it was the first
      // `login()` that blew the budget, not the flow after it, and every later test looked
      // fast only because the first had already paid for the compile.
      "/",
      "/administration/users",
    ]) {
      await request.get(route).catch(() => undefined);
    }
  });

  test("an invitation is sent, accepted, and the new user signs in", async ({ page, request }) => {
    const invited = uniqueEmail("e2e.invited");
    const newPassword = "an invited user's passphrase";

    await login(page);
    const before = emailsSoFar(invited);
    await inviteFromTheUsersScreen(page, invited);

    // The row is on the screen with data behind it — rule 13's bar, and the state the Revoke
    // action below keys on.
    const row = page.getByRole("row").filter({ hasText: invited });
    await expect(row).toContainText(maintenance.pending);

    // The mail, and the link in it. Nothing has handed the browser a token.
    const mail = await waitForEmail(invited, { since: before });
    expect(mail.subject).toContain("invited");
    const token = tokenFrom(mail);

    // Accept as the invited user would: a clean context, no session, the link from the email.
    const guest = await page.context().browser()!.newContext();
    const guestPage = await guest.newPage();
    await guestPage.goto(`/invitations/accept?token=${encodeURIComponent(token)}`);
    await waitForHydration(guestPage, "form");
    await guestPage.getByLabel(auth.fullName).fill("Invited Colleague");
    await guestPage.getByLabel(auth.password).fill(newPassword);
    await guestPage.getByRole("button", { name: auth.acceptInvitation }).click();

    // Accepting *is* signing in — the response is a session, so they land inside the company
    // that invited them rather than at a login form they have no password for yet.
    await guestPage.waitForURL("/");
    await assertNoSeriousViolations(guestPage);

    // And the membership is now active on the inviter's screen — the figure rule 13 asks for.
    await page.reload();
    await expect(page.getByRole("row").filter({ hasText: invited })).toContainText(
      maintenance.active,
    );

    await guest.close();

    // Signing in afresh with the password they chose proves the account is real rather than
    // just that the accept request succeeded.
    //
    // In a **new context**, because accepting already signed them in: `/login` redirects a
    // signed-in user straight to `/`, so reusing the same context would leave this waiting for
    // an email field that is never rendered. (It did, for two minutes, before the page
    // snapshot showed the shell instead of the form.)
    const returning = await page.context().browser()!.newContext();
    const returningPage = await returning.newPage();
    // Through the suite's own `login`, rather than a hand-rolled copy of it. The copy waited
    // on the URL alone and hung on a cold container even though the API had answered 200; the
    // helper waits for the shell to actually render, which is the thing worth asserting and
    // the reason every other spec uses it.
    await login(returningPage, invited, newPassword);
    await returning.close();
  });

  test("an invitation sent to the wrong address is revoked, and the link stops working", async ({
    page,
  }) => {
    const mistyped = uniqueEmail("e2e.mistyped");

    await login(page);
    const before = emailsSoFar(mistyped);
    await inviteFromTheUsersScreen(page, mistyped);
    const token = tokenFrom(await waitForEmail(mistyped, { since: before }));

    const row = page.getByRole("row").filter({ hasText: mistyped });
    await expect(row).toContainText(maintenance.pending);
    await row.getByRole("button", { name: maintenance.revoke }).click();
    await page.getByRole("button", { name: maintenance.revokeInvitation }).click();
    await expect(page.getByText(maintenance.invitationRevoked).first()).toBeVisible();

    // Gone from the list…
    await expect(page.getByRole("row").filter({ hasText: mistyped })).toHaveCount(0);

    // …and the link the recipient may already be holding is dead. This is the half that
    // matters: revoking that only tidied the screen would be worse than none.
    const guest = await page.context().browser()!.newContext();
    const guestPage = await guest.newPage();
    await guestPage.goto(`/invitations/accept?token=${encodeURIComponent(token)}`);
    await waitForHydration(guestPage, "form");
    await guestPage.getByLabel(auth.fullName).fill("Not This Person");
    await guestPage.getByLabel(auth.password).fill("a passphrase they chose");
    await guestPage.getByRole("button", { name: auth.acceptInvitation }).click();
    await expect(guestPage.getByText(auth.acceptFailed)).toBeVisible();
    await guest.close();
  });

  test("a forgotten password is reset, and the old one stops working", async ({ page, request }) => {
    // Done on a user of its own: a reset revokes every session and changes the password, so
    // running it against a shared fixture account would break every later spec.
    const resetting = uniqueEmail("e2e.resetting");
    const firstPassword = "the passphrase they forget";
    const secondPassword = "the passphrase they choose next";

    // Sign this user up through the product, so the reset acts on a real account.
    const signup = await request.post(`${API_BASE}/auth/signup`, {
      data: {
        email: resetting,
        password: firstPassword,
        full_name: "Forgetful Owner",
        company_name: `Reset Co ${Date.now()}`,
      },
    });
    expect(signup.ok()).toBeTruthy();

    const before = emailsSoFar(resetting);
    await page.goto("/login");
    await page.getByRole("link", { name: auth.forgotPassword }).click();
    await expect(page).toHaveURL(/\/forgot-password$/);
    await page.getByLabel(auth.email).fill(resetting);
    await page.getByRole("button", { name: auth.sendResetLink }).click();
    await expect(page.getByText(auth.resetRequested)).toBeVisible();
    await assertNoSeriousViolations(page);

    const token = tokenFrom(await waitForEmail(resetting, { since: before }));
    await page.goto(`/reset-password?token=${encodeURIComponent(token)}`);
    await page.getByLabel(auth.newPassword, { exact: true }).fill(secondPassword);
    await page.getByLabel(auth.confirmPassword).fill(secondPassword);
    await page.getByRole("button", { name: auth.setPassword }).click();
    await expect(page.getByText(auth.resetDone)).toBeVisible();

    // The old password is refused…
    await page.goto("/login");
    await page.getByLabel(auth.email).fill(resetting);
    await page.getByLabel(auth.password).fill(firstPassword);
    await page.getByRole("button", { name: auth.signIn }).click();
    await expect(page).toHaveURL(/\/login$/);

    // …and the new one works.
    await page.getByLabel(auth.password).fill(secondPassword);
    await page.getByRole("button", { name: auth.signIn }).click();
    await page.waitForURL("/");
  });

  test("a spent reset link is refused rather than silently failing", async ({ page, request }) => {
    const twice = uniqueEmail("e2e.twice");
    const signup = await request.post(`${API_BASE}/auth/signup`, {
      data: {
        email: twice,
        password: "a first passphrase here",
        full_name: "Double Clicker",
        company_name: `Twice Co ${Date.now()}`,
      },
    });
    expect(signup.ok()).toBeTruthy();

    const before = emailsSoFar(twice);
    await page.goto("/forgot-password");
    await page.getByLabel(auth.email).fill(twice);
    await page.getByRole("button", { name: auth.sendResetLink }).click();
    await expect(page.getByText(auth.resetRequested)).toBeVisible();
    const token = tokenFrom(await waitForEmail(twice, { since: before }));

    const url = `/reset-password?token=${encodeURIComponent(token)}`;
    await page.goto(url);
    await page.getByLabel(auth.newPassword, { exact: true }).fill("a second passphrase here");
    await page.getByLabel(auth.confirmPassword).fill("a second passphrase here");
    await page.getByRole("button", { name: auth.setPassword }).click();
    await expect(page.getByText(auth.resetDone)).toBeVisible();

    // The same link again: single-use, and the screen says so rather than appearing to work.
    await page.goto(url);
    await page.getByLabel(auth.newPassword, { exact: true }).fill("a third passphrase here");
    await page.getByLabel(auth.confirmPassword).fill("a third passphrase here");
    await page.getByRole("button", { name: auth.setPassword }).click();
    await expect(page.getByText(auth.resetFailed)).toBeVisible();
  });

  test("an unverified user is prompted, asks again, and the link flips the flag", async ({
    page,
    request,
  }) => {
    const unverified = uniqueEmail("e2e.unverified");
    const signup = await request.post(`${API_BASE}/auth/signup`, {
      data: {
        email: unverified,
        password: "an unverified passphrase",
        full_name: "Unverified Owner",
        company_name: `Verify Co ${Date.now()}`,
      },
    });
    expect(signup.ok()).toBeTruthy();

    await page.goto("/login");
    await page.getByLabel(auth.email).fill(unverified);
    await page.getByLabel(auth.password).fill("an unverified passphrase");
    await page.getByRole("button", { name: auth.signIn }).click();

    // The banner is the only thing in the product that asks, so its absence was the gap.
    const banner = page.getByTestId("verify-email-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText(auth.verifyBanner);

    const before = emailsSoFar(unverified);
    await banner.getByRole("button", { name: auth.verifyBannerAction }).click();
    await expect(page.getByText(auth.verifySent).first()).toBeVisible();

    const token = tokenFrom(await waitForEmail(unverified, { since: before }));
    await page.goto(`/verify-email?token=${encodeURIComponent(token)}`);
    await expect(page.getByText(auth.verifyDone)).toBeVisible();
    await assertNoSeriousViolations(page);

    // The flag really moved: the banner is gone on the next page load.
    await page.goto("/");
    await expect(page.getByTestId("verify-email-banner")).toHaveCount(0);
  });

  test("screenshots: the four screens, both themes", async ({ page, request }) => {
    // Rule 13 asks for a committed screenshot with data in it. These are one-shot screens, so
    // "data" is the state a real user arrives in: a sent request, a live token, a dead one.
    const shot = uniqueEmail("e2e.shot");
    await request.post(`${API_BASE}/auth/signup`, {
      data: {
        email: shot,
        password: "a screenshot passphrase",
        full_name: "Screenshot Owner",
        company_name: `Shot Co ${Date.now()}`,
      },
    });
    const before = emailsSoFar(shot);
    await page.goto("/forgot-password");
    await page.getByLabel(auth.email).fill(shot);

    for (const theme of ["light", "dark"] as const) {
      // Navigate first: `setTheme` stamps `data-theme` on the live document, and a navigation
      // afterwards would throw it away — the screenshots would both be light.
      await page.goto("/forgot-password");
      await waitForHydration(page, "form");
      await page.getByLabel(auth.email).fill(shot);
      await setTheme(page, theme);
      await page.screenshot({ path: `../docs/screenshots/p1-gap/forgot-password-${theme}.png` });
    }

    await page.getByRole("button", { name: auth.sendResetLink }).click();
    await expect(page.getByText(auth.resetRequested)).toBeVisible();
    const token = tokenFrom(await waitForEmail(shot, { since: before }));

    for (const theme of ["light", "dark"] as const) {
      await page.goto(`/reset-password?token=${encodeURIComponent(token)}`);
      await waitForHydration(page, "form");
      await page.getByLabel(auth.newPassword, { exact: true }).fill("a brand new passphrase");
      await page.getByLabel(auth.confirmPassword).fill("a brand new passphrase");
      await setTheme(page, theme);
      await page.screenshot({ path: `../docs/screenshots/p1-gap/reset-password-${theme}.png` });

      await page.goto(`/invitations/accept?token=${encodeURIComponent(token)}&company=Rugari+Wines`);
      await waitForHydration(page, "form");
      await page.getByLabel(auth.fullName).fill("Invited Colleague");
      await setTheme(page, theme);
      await page.screenshot({ path: `../docs/screenshots/p1-gap/accept-invitation-${theme}.png` });
    }

    // The verification landing page, in its resolved state. The token above is a *reset*
    // token, so this lands on the refusal panel — which is the state worth showing: it is what
    // a user who clicks an old link actually sees.
    for (const theme of ["light", "dark"] as const) {
      await page.goto(`/verify-email?token=${encodeURIComponent(token)}`);
      await expect(page.getByText(auth.verifyFailed)).toBeVisible();
      await setTheme(page, theme);
      await page.screenshot({ path: `../docs/screenshots/p1-gap/verify-email-${theme}.png` });
    }

    // The banner, on a signed-in unverified user, with the rest of the shell around it.
    await login(page, PRIMARY_EMAIL);
    const pendingInvite = uniqueEmail("e2e.pending");
    await inviteFromTheUsersScreen(page, pendingInvite);
    for (const theme of ["light", "dark"] as const) {
      await page.goto("/administration/users");
      // A pending row is on the screen, so the Revoke action is in the shot — rule 13 asks for
      // data, and for this screen the data *is* the state the action keys on.
      await expect(page.getByRole("row").filter({ hasText: pendingInvite })).toContainText(
        maintenance.pending,
      );
      await setTheme(page, theme);
      await page.screenshot({ path: `../docs/screenshots/p1-gap/users-revoke-${theme}.png` });
    }
    expect(PASSWORD.length).toBeGreaterThan(0);
  });
});
