# Phase 3 (General Ledger) Frontend Testing Guide

This guide provides a comprehensive checklist to verify all GL frontend components are working correctly.

## Prerequisites

1. Backend server running: `cd backend && uvicorn app.main:app --reload`
2. Frontend running: `cd frontend && npm run dev`
3. Logged in as admin user

## Testing Checklist

### 1. Navigation & Menu Items ✅

- [ ] Navigate to http://localhost:3000/dashboard
- [ ] Verify "General Ledger" menu item is visible in sidebar
- [ ] Click on "General Ledger" to expand submenu
- [ ] Verify three submenu items appear:
  - [ ] Chart of Accounts
  - [ ] Journal Entries  
  - [ ] GL Reports

### 2. Chart of Accounts Page (`/gl/accounts`) 📊

#### 2.1 List View
- [ ] Navigate to Chart of Accounts
- [ ] Verify page title shows "Chart of Accounts"
- [ ] Verify table displays with columns:
  - Code
  - Name
  - Type
  - Balance
  - Active
  - Actions

#### 2.2 Create Account
- [ ] Click "Add New Account" button
- [ ] Verify modal opens with fields:
  - [ ] Account Code (required)
  - [ ] Account Name (required)
  - [ ] Account Type (dropdown with ASSET, LIABILITY, EQUITY, INCOME, EXPENSE)
  - [ ] Parent Account (optional dropdown)
  - [ ] Active (checkbox)
- [ ] Fill in test data:
  - Code: "1100"
  - Name: "Test Bank Account"
  - Type: ASSET
- [ ] Click "Create Account"
- [ ] Verify account appears in list
- [ ] Verify modal closes

#### 2.3 Edit Account
- [ ] Click Edit button (pencil icon) on any account
- [ ] Verify modal opens with existing data pre-filled
- [ ] Change account name
- [ ] Click "Save Changes"
- [ ] Verify changes reflect in list

#### 2.4 Delete Account
- [ ] Click Delete button (trash icon) on test account
- [ ] Verify confirmation modal appears
- [ ] Click "Delete"
- [ ] Verify account is removed or marked inactive

### 3. Journal Entries Page (`/gl/journal-entries`) 📝

#### 3.1 List View
- [ ] Navigate to Journal Entries
- [ ] Verify page title shows "Journal Entries"
- [ ] Verify table displays with columns:
  - Entry ID
  - Date
  - Reference
  - Total Debit
  - Total Credit
  - Status
  - Actions

#### 3.2 Create Journal Entry
- [ ] Click "New Journal Entry" button
- [ ] Verify form appears with:
  - [ ] Date field (defaulted to today)
  - [ ] Entry ID (auto-generated)
  - [ ] Reference field
  - [ ] Description field
  - [ ] Journal lines table
- [ ] Verify initial 2 empty lines
- [ ] Fill first line:
  - Account: Select any expense account
  - Description: "Test expense"
  - Debit: 100
- [ ] Fill second line:
  - Account: Select any asset account
  - Description: "Cash payment"
  - Credit: 100
- [ ] Verify totals show balanced (✓ Balanced)
- [ ] Click "Post Entry"
- [ ] Verify entry appears in list

#### 3.3 Add/Remove Lines
- [ ] Create new entry
- [ ] Click "Add Line" button
- [ ] Verify new line appears
- [ ] Click "Remove" on a line
- [ ] Verify line is removed (minimum 2 lines enforced)

#### 3.4 Balance Validation
- [ ] Create entry with unbalanced amounts
- [ ] Verify "✗ Unbalanced" indicator shows
- [ ] Verify "Post Entry" button is disabled

#### 3.5 Reverse Entry
- [ ] Click Reverse button (refresh icon) on posted entry
- [ ] Verify reversal modal appears
- [ ] Enter reversal date
- [ ] Click "Reverse Entry"
- [ ] Verify original entry shows as "Reversed"

### 4. GL Reports Page (`/gl/reports`) 📈

#### 4.1 Trial Balance Tab
- [ ] Navigate to GL Reports
- [ ] Verify "Trial Balance" tab is active
- [ ] Select report date
- [ ] Verify report displays with columns:
  - Account Code
  - Account Name
  - Type
  - Debit
  - Credit
  - Balance
- [ ] Verify totals row at bottom
- [ ] Verify balanced indicator (✓ or ✗)

#### 4.2 Export Functionality
- [ ] Click "Export CSV" button
- [ ] Verify file downloads as `trial_balance_[date].csv`

#### 4.3 GL Detail Tab
- [ ] Click "GL Detail" tab
- [ ] Select an account from dropdown
- [ ] Set date range
- [ ] Verify transactions display with:
  - Date
  - Journal Entry ID
  - Reference
  - Description
  - Debit
  - Credit
  - Running Balance
- [ ] Verify running balance calculates correctly

### 5. Data Validation Tests 🔍

#### 5.1 Account Code Uniqueness
- [ ] Try creating account with existing code
- [ ] Verify error message appears

#### 5.2 Required Fields
- [ ] Try submitting forms with empty required fields
- [ ] Verify validation messages appear

#### 5.3 Date Validation
- [ ] Try posting journal entry with closed period date
- [ ] Verify error message about closed period

### 6. UI/UX Tests 🎨

#### 6.1 Responsive Design
- [ ] Resize browser window
- [ ] Verify tables scroll horizontally on small screens
- [ ] Verify modals remain centered

#### 6.2 Loading States
- [ ] Verify loading indicators appear during data fetch
- [ ] Check "Loading..." messages

#### 6.3 Error Handling
- [ ] Stop backend server
- [ ] Try performing actions
- [ ] Verify error messages appear (not blank screens)

## Automated Test Script

For automated browser testing, you can use this Playwright script:

```javascript
// frontend/tests/phase3-gl.spec.js
const { test, expect } = require('@playwright/test');

test.describe('Phase 3 - General Ledger', () => {
  test.beforeEach(async ({ page }) => {
    // Login
    await page.goto('http://localhost:3000/login');
    await page.fill('input[name="username"]', 'admin');
    await page.fill('input[name="password"]', 'admin123');
    await page.click('button[type="submit"]');
    await page.waitForURL('**/dashboard');
  });

  test('GL menu navigation', async ({ page }) => {
    // Check GL menu exists
    await expect(page.locator('text=General Ledger')).toBeVisible();
    
    // Expand GL menu
    await page.click('text=General Ledger');
    
    // Check submenu items
    await expect(page.locator('text=Chart of Accounts')).toBeVisible();
    await expect(page.locator('text=Journal Entries')).toBeVisible();
    await expect(page.locator('text=GL Reports')).toBeVisible();
  });

  test('Create GL account', async ({ page }) => {
    await page.goto('http://localhost:3000/gl/accounts');
    
    // Click add button
    await page.click('text=Add New Account');
    
    // Fill form
    await page.fill('input[name="account_code"]', 'TEST001');
    await page.fill('input[name="account_name"]', 'Test Account');
    await page.selectOption('select[name="account_type"]', 'ASSET');
    
    // Submit
    await page.click('button:has-text("Create Account")');
    
    // Verify in list
    await expect(page.locator('text=TEST001')).toBeVisible();
  });
});
```

Run with: `npx playwright test tests/phase3-gl.spec.js`

## API Endpoint Verification

Use browser DevTools Network tab to verify correct API calls:

| Feature | Expected API Call |
|---------|------------------|
| List Accounts | GET `/api/gl/accounts` |
| Create Account | POST `/api/gl/accounts` |
| Update Account | PUT `/api/gl/accounts/{id}` |
| Delete Account | DELETE `/api/gl/accounts/{id}` |
| Create Journal Entry | POST `/api/gl/journal-entries` |
| Reverse Entry | POST `/api/gl/journal-entries/{id}/reverse` |
| Trial Balance | GET `/api/gl/reports/trial-balance` |
| GL Detail | GET `/api/gl/reports/gl-detail` |
| Export | GET `/api/gl/reports/trial-balance/export` |

## Success Criteria ✅

Phase 3 is complete when:
- [ ] All navigation items work
- [ ] All CRUD operations function correctly
- [ ] Journal entries balance properly
- [ ] Reports generate accurately
- [ ] Export functionality works
- [ ] No console errors in browser
- [ ] All API endpoints return expected data
- [ ] UI is responsive and user-friendly 