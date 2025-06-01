'use client';

import { useState, useEffect } from 'react';
import { Plus, Edit, Calendar, Lock, Unlock, Search, AlertTriangle } from 'lucide-react';
import { useApi } from '@/hooks/useApi';
import { AccountingPeriodForm } from '@/components/forms/AccountingPeriodForm';

interface AccountingPeriod {
  id: number;
  period_name: string;
  start_date: string;
  end_date: string;
  financial_year: number;
  is_closed: boolean;
  created_at: string;
}

export default function AccountingPeriodsPage() {
  const [periods, setPeriods] = useState<AccountingPeriod[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingPeriod, setEditingPeriod] = useState<AccountingPeriod | null>(null);
  const [financialYearFilter, setFinancialYearFilter] = useState<string>('');
  
  const api = useApi();

  const fetchPeriods = async () => {
    try {
      setIsLoading(true);
      let url = '/api/accounting-periods?include_closed=true';
      if (financialYearFilter) {
        url += `&financial_year=${financialYearFilter}`;
      }
      const response = await api.get(url);
      setPeriods(response.data.items);
      setTotal(response.data.total);
    } catch (error) {
      console.error('Failed to fetch periods:', error);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchPeriods();
  }, [financialYearFilter]);

  const handleClosePeriod = async (periodId: number) => {
    if (!confirm('Are you sure you want to close this period? This action cannot be easily undone.')) return;
    
    try {
      await api.post(`/api/accounting-periods/${periodId}/close`);
      fetchPeriods();
    } catch (error: any) {
      alert(error.response?.data?.detail || 'Failed to close period');
    }
  };

  const handleReopenPeriod = async (periodId: number) => {
    if (!confirm('Are you sure you want to reopen this period?')) return;
    
    try {
      await api.post(`/api/accounting-periods/${periodId}/reopen`);
      fetchPeriods();
    } catch (error: any) {
      alert(error.response?.data?.detail || 'Failed to reopen period');
    }
  };

  const handleDelete = async (periodId: number) => {
    if (!confirm('Are you sure you want to delete this period?')) return;
    
    try {
      await api.delete(`/api/accounting-periods/${periodId}`);
      fetchPeriods();
    } catch (error: any) {
      alert(error.response?.data?.detail || 'Failed to delete period');
    }
  };

  const filteredPeriods = periods.filter(period => 
    period.period_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    period.financial_year.toString().includes(searchTerm)
  );

  const uniqueYears = [...new Set(periods.map(p => p.financial_year))].sort((a, b) => b - a);

  const isCurrentPeriod = (period: AccountingPeriod) => {
    const today = new Date();
    const start = new Date(period.start_date);
    const end = new Date(period.end_date);
    return today >= start && today <= end && !period.is_closed;
  };

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold mb-2">Accounting Periods</h1>
        <p className="text-gray-600">Manage financial years and accounting periods</p>
      </div>

      <div className="bg-white rounded-lg shadow">
        <div className="p-4 border-b flex justify-between items-center">
          <div className="flex gap-4 items-center">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400 h-4 w-4" />
              <input
                type="text"
                placeholder="Search periods..."
                className="pl-10 pr-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
            </div>
            <select
              value={financialYearFilter}
              onChange={(e) => setFinancialYearFilter(e.target.value)}
              className="px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">All Years</option>
              {uniqueYears.map(year => (
                <option key={year} value={year}>{year}</option>
              ))}
            </select>
          </div>
          <button
            onClick={() => setShowCreateModal(true)}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
          >
            <Plus className="h-4 w-4" />
            Add Period
          </button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b bg-gray-50">
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Period Name
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Financial Year
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Start Date
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  End Date
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Status
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {isLoading ? (
                <tr>
                  <td colSpan={6} className="px-6 py-4 text-center text-gray-500">
                    Loading...
                  </td>
                </tr>
              ) : filteredPeriods.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-6 py-4 text-center text-gray-500">
                    No periods found
                  </td>
                </tr>
              ) : (
                filteredPeriods.map((period) => {
                  const isCurrent = isCurrentPeriod(period);
                  
                  return (
                    <tr key={period.id} className={`hover:bg-gray-50 ${isCurrent ? 'bg-blue-50' : ''}`}>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <div className="flex items-center">
                          <Calendar className="h-5 w-5 text-gray-400 mr-2" />
                          <span className="text-sm font-medium text-gray-900">
                            {period.period_name}
                          </span>
                          {isCurrent && (
                            <span className="ml-2 px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-blue-100 text-blue-800">
                              Current
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                        {period.financial_year}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                        {new Date(period.start_date).toLocaleDateString()}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                        {new Date(period.end_date).toLocaleDateString()}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <span
                          className={`px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${
                            period.is_closed
                              ? 'bg-red-100 text-red-800'
                              : 'bg-green-100 text-green-800'
                          }`}
                        >
                          {period.is_closed ? 'Closed' : 'Open'}
                        </span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm font-medium">
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => setEditingPeriod(period)}
                            className="text-indigo-600 hover:text-indigo-900"
                            title="Edit"
                          >
                            <Edit className="h-4 w-4" />
                          </button>
                          {period.is_closed ? (
                            <button
                              onClick={() => handleReopenPeriod(period.id)}
                              className="text-green-600 hover:text-green-900"
                              title="Reopen Period"
                            >
                              <Unlock className="h-4 w-4" />
                            </button>
                          ) : (
                            <button
                              onClick={() => handleClosePeriod(period.id)}
                              className="text-orange-600 hover:text-orange-900"
                              title="Close Period"
                            >
                              <Lock className="h-4 w-4" />
                            </button>
                          )}
                          <button
                            onClick={() => handleDelete(period.id)}
                            className="text-red-600 hover:text-red-900"
                            title="Delete"
                          >
                            <AlertTriangle className="h-4 w-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        <div className="px-6 py-3 border-t text-sm text-gray-500">
          Showing {filteredPeriods.length} of {total} periods
        </div>
      </div>

      {/* Create/Edit Period Modal */}
      {(showCreateModal || editingPeriod) && (
        <AccountingPeriodForm
          period={editingPeriod}
          onClose={() => {
            setShowCreateModal(false);
            setEditingPeriod(null);
          }}
          onSuccess={() => {
            setShowCreateModal(false);
            setEditingPeriod(null);
            fetchPeriods();
          }}
        />
      )}
    </div>
  );
} 