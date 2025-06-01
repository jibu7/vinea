export interface GLAccount {
  id: number;
  company_id: number;
  account_code: string;
  account_name: string;
  account_type: 'ASSET' | 'LIABILITY' | 'EQUITY' | 'INCOME' | 'EXPENSE'; // Match backend choices
  parent_account_id?: number | null;
  current_balance: string; // Keep as string for display, convert from Decimal
  is_active: boolean;
  is_system_account: boolean;
  created_at?: string; 
  updated_at?: string;
}

export type GLAccountFormData = {
  account_code: string;
  account_name: string;
  account_type: 'ASSET' | 'LIABILITY' | 'EQUITY' | 'INCOME' | 'EXPENSE';
  parent_account_id?: number | null;
  is_active?: boolean;
  // company_id will be set by the backend based on the authenticated user
};

export interface GLTransaction {
  id: number;
  company_id: number;
  journal_entry_id: string;
  account_id: number;
  transaction_date: string; // ISO date string
  period_id?: number | null;
  description?: string | null;
  debit_amount: string; // Keep as string for display, convert from Decimal
  credit_amount: string; // Keep as string for display, convert from Decimal
  reference?: string | null;
  source_module?: string | null;
  source_document_id?: number | null;
  posted_by_user_id?: number | null;
  is_reversed: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface JournalEntryLine {
  account_id: number;
  description?: string;
  debit_amount: number;
  credit_amount: number;
}

export interface JournalEntry {
  transaction_date: string;
  journal_entry_id: string;
  reference?: string;
  description?: string;
  lines: JournalEntryLine[];
}

export interface TrialBalanceLine {
  account_id: number;
  account_code: string;
  account_name: string;
  account_type: string;
  debit: string;
  credit: string;
  balance: string;
} 