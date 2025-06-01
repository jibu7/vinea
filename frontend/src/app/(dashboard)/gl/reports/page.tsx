'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from '@/lib/axios';
import { TrialBalanceLine, GLTransaction, GLAccount } from '@/types/gl';
import { FileText, Download } from 'lucide-react';

// Mock/Placeholder components
const Button = ({ onClick, children, className, disabled = false }: any) => 
  <button className={`px-4 py-2 rounded ${className}`} onClick={onClick} disabled={disabled}>{children}</button>;

const Tabs = ({ value, onValueChange, children }: any) => {
  return (
    <div>
      <div className="border-b border-gray-200">
        <nav className="-mb-px flex space-x-8">
          {children.map((child: any, index: number) => (
            <button
              key={index}
              className={`py-2 px-1 border-b-2 font-medium text-sm ${
                value === child.props.value
                  ? 'border-indigo-500 text-indigo-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
              }`}
              onClick={() => onValueChange(child.props.value)}
            >
              {child.props.label}
            </button>
          ))}
        </nav>
      </div>
      <div className="mt-4">
        {children.find((child: any) => child.props.value === value)?.props.children}
      </div>
    </div>
  );
};

const TabPanel = ({ value, label, children }: any) => <>{children}</>;

// API functions
const fetchTrialBalance = async (reportDate: string): Promise<TrialBalanceLine[]> => {
  const response = await axios.get('/gl/reports/trial-balance', {
    params: { report_date: reportDate }
  });
  return response.data;
};

const fetchGLDetail = async (accountId: number, startDate: string, endDate: string): Promise<GLTransaction[]> => {
  const response = await axios.get('/gl/reports/gl-detail', {
    params: { 
      account_id: accountId,
      start_date: startDate,
      end_date: endDate
    }
  });
  return response.data;
};

const fetchGLAccounts = async (): Promise<GLAccount[]> => {
  const response = await axios.get('/gl/accounts');
  return response.data;
};

const exportTrialBalance = async (reportDate: string, format: string = 'csv') => {
  const response = await axios.get('/gl/reports/trial-balance/export', {
    params: { report_date: reportDate, format },
    responseType: 'blob'
  });
  
  // Create download link
  const url = window.URL.createObjectURL(new Blob([response.data]));
  const link = document.createElement('a');
  link.href = url;
  link.setAttribute('download', `trial_balance_${reportDate}.${format}`);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
};

export default function GLReportsPage() {
  const [activeTab, setActiveTab] = useState('trial-balance');
  const [reportDate, setReportDate] = useState(new Date().toISOString().split('T')[0]);
  const [selectedAccountId, setSelectedAccountId] = useState<number>(0);
  const [startDate, setStartDate] = useState(new Date(new Date().getFullYear(), 0, 1).toISOString().split('T')[0]);
  const [endDate, setEndDate] = useState(new Date().toISOString().split('T')[0]);

  const { data: accounts } = useQuery<GLAccount[]>({
    queryKey: ['glAccountsForReports'],
    queryFn: fetchGLAccounts,
  });

  const { data: trialBalance, isLoading: loadingTB } = useQuery<TrialBalanceLine[]>({
    queryKey: ['trialBalance', reportDate],
    queryFn: () => fetchTrialBalance(reportDate),
    enabled: activeTab === 'trial-balance'
  });

  const { data: glDetail, isLoading: loadingDetail } = useQuery<GLTransaction[]>({
    queryKey: ['glDetail', selectedAccountId, startDate, endDate],
    queryFn: () => fetchGLDetail(selectedAccountId, startDate, endDate),
    enabled: activeTab === 'gl-detail' && selectedAccountId > 0
  });

  // Calculate totals for trial balance
  const trialBalanceTotals = trialBalance?.reduce(
    (totals, line) => ({
      debit: totals.debit + parseFloat(line.debit),
      credit: totals.credit + parseFloat(line.credit)
    }),
    { debit: 0, credit: 0 }
  );

  return (
    <div className="container mx-auto p-4">
      <h1 className="text-2xl font-semibold mb-6">General Ledger Reports</h1>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabPanel value="trial-balance" label="Trial Balance">
          <div className="mb-4 flex items-end gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700">Report Date</label>
              <input 
                type="date" 
                value={reportDate} 
                onChange={(e) => setReportDate(e.target.value)}
                className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
              />
            </div>
            <Button 
              onClick={() => exportTrialBalance(reportDate)} 
              className="bg-green-600 hover:bg-green-700 text-white"
            >
              <Download className="inline-block mr-2 h-4 w-4" /> Export CSV
            </Button>
          </div>

          {loadingTB ? (
            <p>Loading trial balance...</p>
          ) : (
            <div className="bg-white shadow rounded-lg overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Account Code</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Account Name</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Type</th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Debit</th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Credit</th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Balance</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {trialBalance?.map((line) => (
                    <tr key={line.account_id}>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700">{line.account_code}</td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700">{line.account_name}</td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{line.account_type}</td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700 text-right">
                        {parseFloat(line.debit) > 0 ? parseFloat(line.debit).toFixed(2) : '-'}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700 text-right">
                        {parseFloat(line.credit) > 0 ? parseFloat(line.credit).toFixed(2) : '-'}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700 text-right font-medium">
                        {parseFloat(line.balance).toFixed(2)}
                      </td>
                    </tr>
                  ))}
                  {trialBalance?.length === 0 && (
                    <tr>
                      <td colSpan={6} className="px-6 py-4 text-center text-sm text-gray-500">No data available for selected date.</td>
                    </tr>
                  )}
                </tbody>
                {trialBalance && trialBalance.length > 0 && (
                  <tfoot className="bg-gray-50">
                    <tr>
                      <td colSpan={3} className="px-6 py-3 text-right font-semibold">Totals:</td>
                      <td className="px-6 py-3 text-right font-semibold">{trialBalanceTotals?.debit.toFixed(2)}</td>
                      <td className="px-6 py-3 text-right font-semibold">{trialBalanceTotals?.credit.toFixed(2)}</td>
                      <td className="px-6 py-3 text-right">
                        {trialBalanceTotals && Math.abs(trialBalanceTotals.debit - trialBalanceTotals.credit) < 0.01 ? 
                          <span className="text-green-600">✓ Balanced</span> : 
                          <span className="text-red-600">✗ Unbalanced</span>
                        }
                      </td>
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          )}
        </TabPanel>

        <TabPanel value="gl-detail" label="GL Detail">
          <div className="mb-4 grid grid-cols-4 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700">Account</label>
              <select 
                value={selectedAccountId} 
                onChange={(e) => setSelectedAccountId(parseInt(e.target.value))}
                className="mt-1 block w-full px-3 py-2 border border-gray-300 bg-white rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
              >
                <option value="0">Select Account</option>
                {accounts?.map(acc => (
                  <option key={acc.id} value={acc.id}>
                    {acc.account_code} - {acc.account_name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700">Start Date</label>
              <input 
                type="date" 
                value={startDate} 
                onChange={(e) => setStartDate(e.target.value)}
                className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700">End Date</label>
              <input 
                type="date" 
                value={endDate} 
                onChange={(e) => setEndDate(e.target.value)}
                className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
              />
            </div>
          </div>

          {selectedAccountId === 0 ? (
            <p className="text-gray-500">Please select an account to view details.</p>
          ) : loadingDetail ? (
            <p>Loading GL detail...</p>
          ) : (
            <div className="bg-white shadow rounded-lg overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Date</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Journal Entry</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Reference</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Description</th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Debit</th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Credit</th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Running Balance</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {glDetail?.map((trans, index) => {
                    // Calculate running balance
                    const runningBalance = glDetail.slice(0, index + 1).reduce(
                      (sum, t) => sum + parseFloat(t.debit_amount) - parseFloat(t.credit_amount),
                      0
                    );
                    
                    return (
                      <tr key={trans.id} className={trans.is_reversed ? 'bg-red-50' : ''}>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700">{trans.transaction_date}</td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700">{trans.journal_entry_id}</td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700">{trans.reference || '-'}</td>
                        <td className="px-6 py-4 text-sm text-gray-700">{trans.description || '-'}</td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700 text-right">
                          {parseFloat(trans.debit_amount) > 0 ? parseFloat(trans.debit_amount).toFixed(2) : '-'}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700 text-right">
                          {parseFloat(trans.credit_amount) > 0 ? parseFloat(trans.credit_amount).toFixed(2) : '-'}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700 text-right font-medium">
                          {runningBalance.toFixed(2)}
                        </td>
                      </tr>
                    );
                  })}
                  {glDetail?.length === 0 && (
                    <tr>
                      <td colSpan={7} className="px-6 py-4 text-center text-sm text-gray-500">No transactions found for selected criteria.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </TabPanel>
      </Tabs>
    </div>
  );
} 