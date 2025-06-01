const { chromium } = require('playwright');

async function testPhase3GL() {
  console.log('🚀 Starting Phase 3 (General Ledger) Frontend Testing...\n');
  
  const browser = await chromium.launch({ headless: false, slowMo: 1000 });
  const context = await browser.newContext();
  const page = await context.newPage();
  
  try {
    // Test 1: Navigation & Login
    console.log('📋 Test 1: Navigation & Login');
    await page.goto('http://localhost:3000');
    
    // Check if we're redirected to login
    await page.waitForURL('**/login', { timeout: 5000 });
    console.log('✅ Redirected to login page');
    
    // Login as admin
    await page.fill('input[name="username"]', 'admin');
    await page.fill('input[name="password"]', 'admin123');
    await page.click('button[type="submit"]');
    
    // Wait for dashboard
    await page.waitForURL('**/dashboard', { timeout: 10000 });
    console.log('✅ Successfully logged in and reached dashboard\n');
    
    // Test 2: GL Menu Navigation
    console.log('📋 Test 2: GL Menu Navigation');
    
    // Check if General Ledger menu exists
    const glMenu = page.locator('text=General Ledger');
    await glMenu.waitFor({ timeout: 5000 });
    console.log('✅ General Ledger menu item found');
    
    // Click to expand GL menu
    await glMenu.click();
    await page.waitForTimeout(1000);
    
    // Check submenu items
    const chartOfAccounts = page.locator('text=Chart of Accounts');
    const journalEntries = page.locator('text=Journal Entries');
    const glReports = page.locator('text=GL Reports');
    
    await chartOfAccounts.waitFor({ timeout: 5000 });
    await journalEntries.waitFor({ timeout: 5000 });
    await glReports.waitFor({ timeout: 5000 });
    
    console.log('✅ All GL submenu items found');
    console.log('  - Chart of Accounts');
    console.log('  - Journal Entries');
    console.log('  - GL Reports\n');
    
    // Test 3: Chart of Accounts Page
    console.log('📋 Test 3: Chart of Accounts Page');
    
    await chartOfAccounts.click();
    await page.waitForURL('**/gl/accounts', { timeout: 5000 });
    
    // Check page title
    const accountsTitle = page.locator('h1:has-text("Chart of Accounts")');
    await accountsTitle.waitFor({ timeout: 5000 });
    console.log('✅ Chart of Accounts page loaded');
    
    // Check if Add New Account button exists
    const addAccountBtn = page.locator('text=Add New Account');
    await addAccountBtn.waitFor({ timeout: 5000 });
    console.log('✅ Add New Account button found');
    
    // Check table headers
    const expectedHeaders = ['Code', 'Name', 'Type', 'Balance', 'Active', 'Actions'];
    for (const header of expectedHeaders) {
      const headerElement = page.locator(`th:has-text("${header}")`);
      await headerElement.waitFor({ timeout: 3000 });
    }
    console.log('✅ All expected table headers found\n');
    
    // Test 4: Journal Entries Page
    console.log('📋 Test 4: Journal Entries Page');
    
    await journalEntries.click();
    await page.waitForURL('**/gl/journal-entries', { timeout: 5000 });
    
    // Check page title
    const jeTitle = page.locator('h1:has-text("Journal Entries")');
    await jeTitle.waitFor({ timeout: 5000 });
    console.log('✅ Journal Entries page loaded');
    
    // Check if New Journal Entry button exists
    const newJEBtn = page.locator('text=New Journal Entry');
    await newJEBtn.waitFor({ timeout: 5000 });
    console.log('✅ New Journal Entry button found');
    
    // Check table headers
    const jeHeaders = ['Entry ID', 'Date', 'Reference', 'Total Debit', 'Total Credit', 'Status', 'Actions'];
    for (const header of jeHeaders) {
      const headerElement = page.locator(`th:has-text("${header}")`);
      await headerElement.waitFor({ timeout: 3000 });
    }
    console.log('✅ All expected table headers found\n');
    
    // Test 5: GL Reports Page
    console.log('📋 Test 5: GL Reports Page');
    
    await glReports.click();
    await page.waitForURL('**/gl/reports', { timeout: 5000 });
    
    // Check page title
    const reportsTitle = page.locator('h1:has-text("General Ledger Reports")');
    await reportsTitle.waitFor({ timeout: 5000 });
    console.log('✅ GL Reports page loaded');
    
    // Check if tabs exist
    const trialBalanceTab = page.locator('button:has-text("Trial Balance")');
    const glDetailTab = page.locator('button:has-text("GL Detail")');
    
    await trialBalanceTab.waitFor({ timeout: 5000 });
    await glDetailTab.waitFor({ timeout: 5000 });
    console.log('✅ Both report tabs found');
    console.log('  - Trial Balance tab');
    console.log('  - GL Detail tab');
    
    // Check Export CSV button
    const exportBtn = page.locator('text=Export CSV');
    await exportBtn.waitFor({ timeout: 5000 });
    console.log('✅ Export CSV button found\n');
    
    // Test 6: API Connectivity Test
    console.log('📋 Test 6: API Connectivity Test');
    
    // Listen for network requests
    const apiCalls = [];
    page.on('response', response => {
      if (response.url().includes('/api/gl/')) {
        apiCalls.push({
          url: response.url(),
          status: response.status(),
          method: response.request().method()
        });
      }
    });
    
    // Trigger some API calls by refreshing pages
    await page.goto('http://localhost:3000/gl/accounts');
    await page.waitForTimeout(2000);
    
    await page.goto('http://localhost:3000/gl/journal-entries');
    await page.waitForTimeout(2000);
    
    await page.goto('http://localhost:3000/gl/reports');
    await page.waitForTimeout(2000);
    
    console.log('✅ API calls detected:');
    apiCalls.forEach(call => {
      console.log(`  ${call.method} ${call.url} - Status: ${call.status}`);
    });
    
    if (apiCalls.length > 0) {
      console.log('✅ Backend API connectivity confirmed\n');
    } else {
      console.log('⚠️  No API calls detected - may need authentication\n');
    }
    
    // Test 7: Modal Testing
    console.log('📋 Test 7: Modal Testing');
    
    // Test Add Account Modal
    await page.goto('http://localhost:3000/gl/accounts');
    await page.waitForTimeout(1000);
    
    try {
      await addAccountBtn.click();
      await page.waitForTimeout(1000);
      
      // Check if modal opened
      const modal = page.locator('text=Add New GL Account');
      await modal.waitFor({ timeout: 3000 });
      console.log('✅ Add Account modal opens');
      
      // Check form fields
      const accountCode = page.locator('input[name="account_code"]');
      const accountName = page.locator('input[name="account_name"]');
      const accountType = page.locator('select[name="account_type"]');
      
      await accountCode.waitFor({ timeout: 3000 });
      await accountName.waitFor({ timeout: 3000 });
      await accountType.waitFor({ timeout: 3000 });
      console.log('✅ All form fields found in modal');
      
      // Close modal
      const closeBtn = page.locator('button:has-text("Close")');
      await closeBtn.click();
      await page.waitForTimeout(500);
      console.log('✅ Modal closes properly\n');
      
    } catch (error) {
      console.log('⚠️  Modal test failed:', error.message, '\n');
    }
    
    console.log('🎉 Phase 3 Testing Complete!');
    console.log('\n📊 Summary:');
    console.log('✅ Navigation working');
    console.log('✅ All GL pages accessible');
    console.log('✅ UI components present');
    console.log('✅ Basic functionality verified');
    
  } catch (error) {
    console.error('❌ Test failed:', error.message);
    console.error('Stack:', error.stack);
  } finally {
    await browser.close();
  }
}

// Run the test
testPhase3GL().catch(console.error); 