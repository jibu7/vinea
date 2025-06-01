'use client';

import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import axios from '@/lib/axios';
import { GLAccount, GLTransaction, JournalEntry, JournalEntryLine } from '@/types/gl';
import { PlusCircle, FileText, RefreshCw } from 'lucide-react';

// Mock/Placeholder components - replace with your actual UI library components
const Button = ({ onClick, children, className, type = 'button', disabled = false }: any) => 
  <button type={type} className={`px-4 py-2 rounded ${className}`} onClick={onClick} disabled={disabled}>{children}</button>;

const Modal = ({ isOpen, onClose, title, children }: any) => 
  isOpen ? (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white p-6 rounded-lg shadow-xl w-3/4 max-w-4xl max-h-[90vh] overflow-y-auto">
        <h2 className="text-xl font-semibold mb-4">{title}</h2>
        {children}
      </div>
    </div>
  ) : null;

// API functions
const fetchGLAccounts = async (): Promise<GLAccount[]> => {
  const response = await axios.get('/gl/accounts?is_active=true');
  return response.data;
};

const fetchJournalEntries = async (): Promise<GLTransaction[]> => {
  const response = await axios.get('/gl/reports/gl-detail', {
    params: {
      start_date: new Date(new Date().getFullYear(), 0, 1).toISOString().split('T')[0],
      end_date: new Date().toISOString().split('T')[0],
    }
  });
  return response.data;
};

const createJournalEntry = async (data: JournalEntry): Promise<GLTransaction[]> => {
  const response = await axios.post('/gl/journal-entries', data);
  return response.data;
};

const reverseJournalEntry = async ({ 
  journalEntryId, 
  reversalDate, 
  reversalReference 
}: { 
  journalEntryId: string;
  reversalDate: string;
  reversalReference?: string;
}): Promise<GLTransaction[]> => {
  const response = await axios.post(`/gl/journal-entries/${journalEntryId}/reverse`, {
    reversal_date: reversalDate,
    reversal_reference: reversalReference
  });
  return response.data;
};

export default function GLJournalEntriesPage() {
  const queryClient = useQueryClient();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedJournalId, setSelectedJournalId] = useState<string | null>(null);
  const [isReverseModalOpen, setIsReverseModalOpen] = useState(false);

  const { data: accounts } = useQuery<GLAccount[]>({
    queryKey: ['glAccountsForJE'],
    queryFn: fetchGLAccounts,
  });

  const { data: journalEntries, isLoading } = useQuery<GLTransaction[]>({
    queryKey: ['journalEntries'],
    queryFn: fetchJournalEntries,
  });

  const createMutation = useMutation<GLTransaction[], Error, JournalEntry>({
    mutationFn: createJournalEntry,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['journalEntries'] });
      queryClient.invalidateQueries({ queryKey: ['glAccounts'] }); // Update account balances
      setIsModalOpen(false);
    },
    onError: (error) => {
      console.error('Failed to create journal entry:', error);
      // TODO: Show user-friendly error message
    },
  });

  const reverseMutation = useMutation({
    mutationFn: reverseJournalEntry,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['journalEntries'] });
      queryClient.invalidateQueries({ queryKey: ['glAccounts'] });
      setIsReverseModalOpen(false);
      setSelectedJournalId(null);
    },
    onError: (error) => {
      console.error('Failed to reverse journal entry:', error);
    },
  });

  // Group transactions by journal entry ID
  const groupedEntries = journalEntries?.reduce((acc, trans) => {
    if (!acc[trans.journal_entry_id]) {
      acc[trans.journal_entry_id] = [];
    }
    acc[trans.journal_entry_id].push(trans);
    return acc;
  }, {} as Record<string, GLTransaction[]>) || {};

  if (isLoading) return <p>Loading journal entries...</p>;

  return (
    <div className="container mx-auto p-4">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-semibold">Journal Entries</h1>
        <Button onClick={() => setIsModalOpen(true)} className="bg-primary text-white hover:bg-primary-dark">
          <PlusCircle className="inline-block mr-2 h-5 w-5" /> New Journal Entry
        </Button>
      </div>

      <div className="bg-white shadow rounded-lg overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Entry ID</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Date</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Reference</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Total Debit</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Total Credit</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Actions</th>
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-gray-200">
            {Object.entries(groupedEntries).map(([journalId, transactions]) => {
              const firstTrans = transactions[0];
              const totalDebit = transactions.reduce((sum, t) => sum + parseFloat(t.debit_amount), 0);
              const totalCredit = transactions.reduce((sum, t) => sum + parseFloat(t.credit_amount), 0);
              const isReversed = transactions.some(t => t.is_reversed);
              
              return (
                <tr key={journalId}>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700">{journalId}</td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700">{firstTrans.transaction_date}</td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700">{firstTrans.reference || '-'}</td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700 text-right">{totalDebit.toFixed(2)}</td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700 text-right">{totalCredit.toFixed(2)}</td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                    {isReversed ? 
                      <span className="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-red-100 text-red-800">Reversed</span> : 
                      <span className="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-green-100 text-green-800">Posted</span>
                    }
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm font-medium">
                    <Button 
                      onClick={() => {
                        setSelectedJournalId(journalId);
                        setIsReverseModalOpen(true);
                      }} 
                      className="text-orange-600 hover:text-orange-900"
                      disabled={isReversed}
                    >
                      <RefreshCw className="h-4 w-4" />
                    </Button>
                  </td>
                </tr>
              );
            })}
            {Object.keys(groupedEntries).length === 0 && (
              <tr>
                <td colSpan={7} className="px-6 py-4 text-center text-sm text-gray-500">No journal entries found.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <JournalEntryModal 
        isOpen={isModalOpen} 
        onClose={() => setIsModalOpen(false)} 
        onSubmit={(data) => createMutation.mutate(data)}
        accounts={accounts || []}
      />

      <ReverseJournalModal
        isOpen={isReverseModalOpen}
        onClose={() => {
          setIsReverseModalOpen(false);
          setSelectedJournalId(null);
        }}
        onSubmit={(data) => {
          if (selectedJournalId) {
            reverseMutation.mutate({
              journalEntryId: selectedJournalId,
              ...data
            });
          }
        }}
        journalId={selectedJournalId}
      />
    </div>
  );
}

// Journal Entry Form Modal
interface JournalEntryModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (data: JournalEntry) => void;
  accounts: GLAccount[];
}

function JournalEntryModal({ isOpen, onClose, onSubmit, accounts }: JournalEntryModalProps) {
  const [formData, setFormData] = useState<JournalEntry>({
    transaction_date: new Date().toISOString().split('T')[0],
    journal_entry_id: generateJournalEntryId(),
    reference: '',
    description: '',
    lines: [
      { account_id: 0, description: '', debit_amount: 0, credit_amount: 0 },
      { account_id: 0, description: '', debit_amount: 0, credit_amount: 0 },
    ]
  });

  useEffect(() => {
    if (isOpen) {
      setFormData({
        transaction_date: new Date().toISOString().split('T')[0],
        journal_entry_id: generateJournalEntryId(),
        reference: '',
        description: '',
        lines: [
          { account_id: 0, description: '', debit_amount: 0, credit_amount: 0 },
          { account_id: 0, description: '', debit_amount: 0, credit_amount: 0 },
        ]
      });
    }
  }, [isOpen]);

  const handleLineChange = (index: number, field: keyof JournalEntryLine, value: any) => {
    const newLines = [...formData.lines];
    newLines[index] = { ...newLines[index], [field]: value };
    
    // Clear opposite amount when entering debit/credit
    if (field === 'debit_amount' && value > 0) {
      newLines[index].credit_amount = 0;
    } else if (field === 'credit_amount' && value > 0) {
      newLines[index].debit_amount = 0;
    }
    
    setFormData({ ...formData, lines: newLines });
  };

  const addLine = () => {
    setFormData({
      ...formData,
      lines: [...formData.lines, { account_id: 0, description: '', debit_amount: 0, credit_amount: 0 }]
    });
  };

  const removeLine = (index: number) => {
    if (formData.lines.length > 2) {
      setFormData({
        ...formData,
        lines: formData.lines.filter((_, i) => i !== index)
      });
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    
    // Filter out empty lines and prepare data
    const validLines = formData.lines.filter(line => 
      line.account_id > 0 && (line.debit_amount > 0 || line.credit_amount > 0)
    );
    
    if (validLines.length < 2) {
      alert('Journal entry must have at least 2 valid lines');
      return;
    }
    
    const totalDebit = validLines.reduce((sum, line) => sum + parseFloat(line.debit_amount.toString()), 0);
    const totalCredit = validLines.reduce((sum, line) => sum + parseFloat(line.credit_amount.toString()), 0);
    
    if (Math.abs(totalDebit - totalCredit) > 0.01) {
      alert(`Debits (${totalDebit.toFixed(2)}) must equal credits (${totalCredit.toFixed(2)})`);
      return;
    }
    
    onSubmit({
      ...formData,
      lines: validLines
    });
  };

  const totalDebit = formData.lines.reduce((sum, line) => sum + parseFloat(line.debit_amount.toString()), 0);
  const totalCredit = formData.lines.reduce((sum, line) => sum + parseFloat(line.credit_amount.toString()), 0);
  const isBalanced = Math.abs(totalDebit - totalCredit) < 0.01;

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Create Journal Entry">
      <form onSubmit={handleSubmit}>
        <div className="grid grid-cols-3 gap-4 mb-4">
          <div>
            <label className="block text-sm font-medium text-gray-700">Date</label>
            <input 
              type="date" 
              value={formData.transaction_date} 
              onChange={(e) => setFormData({ ...formData, transaction_date: e.target.value })}
              className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
              required 
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">Entry ID</label>
            <input 
              type="text" 
              value={formData.journal_entry_id} 
              onChange={(e) => setFormData({ ...formData, journal_entry_id: e.target.value })}
              className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
              required 
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">Reference</label>
            <input 
              type="text" 
              value={formData.reference} 
              onChange={(e) => setFormData({ ...formData, reference: e.target.value })}
              className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
            />
          </div>
        </div>
        
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700">Description</label>
          <input 
            type="text" 
            value={formData.description} 
            onChange={(e) => setFormData({ ...formData, description: e.target.value })}
            className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
          />
        </div>

        <div className="mb-4">
          <h3 className="text-sm font-medium text-gray-700 mb-2">Journal Lines</h3>
          <table className="min-w-full divide-y divide-gray-200 border">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Account</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Description</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Debit</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Credit</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Action</th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {formData.lines.map((line, index) => (
                <tr key={index}>
                  <td className="px-3 py-2">
                    <select 
                      value={line.account_id} 
                      onChange={(e) => handleLineChange(index, 'account_id', parseInt(e.target.value))}
                      className="block w-full px-2 py-1 border border-gray-300 rounded-md text-sm"
                      required
                    >
                      <option value="0">Select Account</option>
                      {accounts.map(acc => (
                        <option key={acc.id} value={acc.id}>
                          {acc.account_code} - {acc.account_name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-3 py-2">
                    <input 
                      type="text" 
                      value={line.description} 
                      onChange={(e) => handleLineChange(index, 'description', e.target.value)}
                      className="block w-full px-2 py-1 border border-gray-300 rounded-md text-sm"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <input 
                      type="number" 
                      step="0.01" 
                      min="0"
                      value={line.debit_amount} 
                      onChange={(e) => handleLineChange(index, 'debit_amount', parseFloat(e.target.value) || 0)}
                      className="block w-full px-2 py-1 border border-gray-300 rounded-md text-sm"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <input 
                      type="number" 
                      step="0.01" 
                      min="0"
                      value={line.credit_amount} 
                      onChange={(e) => handleLineChange(index, 'credit_amount', parseFloat(e.target.value) || 0)}
                      className="block w-full px-2 py-1 border border-gray-300 rounded-md text-sm"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <Button 
                      onClick={() => removeLine(index)} 
                      className="text-red-600 hover:text-red-900 text-sm"
                      disabled={formData.lines.length <= 2}
                    >
                      Remove
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot className="bg-gray-50">
              <tr>
                <td colSpan={2} className="px-3 py-2 text-right font-semibold">Totals:</td>
                <td className="px-3 py-2 font-semibold">{totalDebit.toFixed(2)}</td>
                <td className="px-3 py-2 font-semibold">{totalCredit.toFixed(2)}</td>
                <td className="px-3 py-2">
                  {isBalanced ? 
                    <span className="text-green-600 text-sm">✓ Balanced</span> : 
                    <span className="text-red-600 text-sm">✗ Unbalanced</span>
                  }
                </td>
              </tr>
            </tfoot>
          </table>
          
          <Button onClick={addLine} className="mt-2 bg-gray-200 hover:bg-gray-300 text-black text-sm">
            Add Line
          </Button>
        </div>

        <div className="flex justify-end space-x-3">
          <Button type="button" onClick={onClose} className="bg-gray-200 hover:bg-gray-300 text-black">
            Cancel
          </Button>
          <Button type="submit" className="bg-primary hover:bg-primary-dark text-white" disabled={!isBalanced}>
            Post Entry
          </Button>
        </div>
      </form>
    </Modal>
  );
}

// Reverse Journal Modal
interface ReverseJournalModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (data: { reversalDate: string; reversalReference?: string }) => void;
  journalId: string | null;
}

function ReverseJournalModal({ isOpen, onClose, onSubmit, journalId }: ReverseJournalModalProps) {
  const [reversalDate, setReversalDate] = useState(new Date().toISOString().split('T')[0]);
  const [reversalReference, setReversalReference] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({ reversalDate, reversalReference });
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Reverse Journal Entry">
      <form onSubmit={handleSubmit}>
        <p className="mb-4">Reversing journal entry: <strong>{journalId}</strong></p>
        
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700">Reversal Date</label>
          <input 
            type="date" 
            value={reversalDate} 
            onChange={(e) => setReversalDate(e.target.value)}
            className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
            required 
          />
        </div>
        
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700">Reversal Reference</label>
          <input 
            type="text" 
            value={reversalReference} 
            onChange={(e) => setReversalReference(e.target.value)}
            placeholder="Optional reference for reversal"
            className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
          />
        </div>

        <div className="flex justify-end space-x-3">
          <Button type="button" onClick={onClose} className="bg-gray-200 hover:bg-gray-300 text-black">
            Cancel
          </Button>
          <Button type="submit" className="bg-orange-600 hover:bg-orange-700 text-white">
            Reverse Entry
          </Button>
        </div>
      </form>
    </Modal>
  );
}

// Helper function to generate journal entry ID
function generateJournalEntryId(): string {
  const date = new Date();
  const timestamp = date.toISOString().replace(/[^0-9]/g, '').slice(0, 14);
  return `JE-${timestamp}`;
} 