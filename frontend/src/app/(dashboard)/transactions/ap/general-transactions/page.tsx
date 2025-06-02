'use client'

import { useState, useEffect } from 'react'
import { useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { Plus, Filter, FileText, CreditCard, FileDown, Settings } from 'lucide-react'
import { api } from '@/lib/api'
import { formatCurrency, formatDate } from '@/lib/utils'

interface APTransaction {
  id: number
  transaction_number: string
  transaction_date: string
  due_date?: string
  supplier_name?: string
  transaction_type_name?: string
  reference?: string
  description?: string
  amount: string
  allocated_amount: string
  is_posted: boolean
  is_allocated: boolean
}

interface Supplier {
  id: number
  supplier_code: string
  name: string
}

interface TransactionType {
  id: number
  code: string
  name: string
  affects_balance: string
}

export default function APTransactionsPage() {
  const searchParams = useSearchParams()
  const supplierIdParam = searchParams.get('supplier_id')
  
  const [transactions, setTransactions] = useState<APTransaction[]>([])
  const [suppliers, setSuppliers] = useState<Supplier[]>([])
  const [transactionTypes, setTransactionTypes] = useState<TransactionType[]>([])
  const [loading, setLoading] = useState(true)
  
  // Filters
  const [selectedSupplier, setSelectedSupplier] = useState(supplierIdParam || '')
  const [selectedType, setSelectedType] = useState('')
  const [postedFilter, setPostedFilter] = useState<string>('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')

  useEffect(() => {
    fetchInitialData()
  }, [])

  useEffect(() => {
    fetchTransactions()
  }, [selectedSupplier, selectedType, postedFilter, dateFrom, dateTo])

  const fetchInitialData = async () => {
    try {
      const [suppliersRes, typesRes] = await Promise.all([
        api.get('/suppliers?is_active=true'),
        api.get('/ap/transaction-types?is_active=true')
      ])
      setSuppliers(suppliersRes.data)
      setTransactionTypes(typesRes.data)
    } catch (error) {
      console.error('Failed to fetch initial data:', error)
    }
  }

  const fetchTransactions = async () => {
    try {
      setLoading(true)
      const params = new URLSearchParams()
      if (selectedSupplier) params.append('supplier_id', selectedSupplier)
      if (selectedType) params.append('transaction_type_id', selectedType)
      if (postedFilter) params.append('is_posted', postedFilter)
      if (dateFrom) params.append('from_date', dateFrom)
      if (dateTo) params.append('to_date', dateTo)
      
      const response = await api.get(`/ap/transactions?${params}`)
      setTransactions(response.data)
    } catch (error) {
      console.error('Failed to fetch transactions:', error)
    } finally {
      setLoading(false)
    }
  }

  const handlePost = async (transactionId: number) => {
    if (!confirm('Are you sure you want to post this transaction?')) return
    
    try {
      await api.post(`/ap/transactions/${transactionId}/post`)
      await fetchTransactions()
    } catch (error: any) {
      console.error('Failed to post transaction:', error)
      alert(error.response?.data?.detail || 'Failed to post transaction')
    }
  }

  const getOutstandingAmount = (transaction: APTransaction) => {
    const amount = parseFloat(transaction.amount)
    const allocated = parseFloat(transaction.allocated_amount)
    return amount - allocated
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold">Accounts Payable</h1>
        <div className="flex space-x-2">
          <Link
            href="/ap/transaction-types"
            className="flex items-center px-4 py-2 border border-gray-300 rounded hover:bg-gray-50"
          >
            <Settings className="h-4 w-4 mr-2" />
            Transaction Types
          </Link>
          <Link
            href="/ap/reports"
            className="flex items-center px-4 py-2 border border-gray-300 rounded hover:bg-gray-50"
          >
            <FileDown className="h-4 w-4 mr-2" />
            Reports
          </Link>
          <Link
            href="/ap/transactions/new"
            className="flex items-center px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
          >
            <Plus className="h-4 w-4 mr-2" />
            New Transaction
          </Link>
        </div>
      </div>

      {/* Filters */}
      <div className="bg-white p-4 rounded-lg shadow">
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Supplier
            </label>
            <select
              value={selectedSupplier}
              onChange={(e) => setSelectedSupplier(e.target.value)}
              className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">All Suppliers</option>
              {suppliers.map((supplier) => (
                <option key={supplier.id} value={supplier.id}>
                  {supplier.supplier_code} - {supplier.name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Type
            </label>
            <select
              value={selectedType}
              onChange={(e) => setSelectedType(e.target.value)}
              className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">All Types</option>
              {transactionTypes.map((type) => (
                <option key={type.id} value={type.id}>
                  {type.name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Status
            </label>
            <select
              value={postedFilter}
              onChange={(e) => setPostedFilter(e.target.value)}
              className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">All</option>
              <option value="true">Posted</option>
              <option value="false">Unposted</option>
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              From Date
            </label>
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              To Date
            </label>
            <input
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <div className="flex items-end">
            <button
              onClick={() => {
                setSelectedSupplier('')
                setSelectedType('')
                setPostedFilter('')
                setDateFrom('')
                setDateTo('')
              }}
              className="w-full px-4 py-2 border border-gray-300 rounded hover:bg-gray-50"
            >
              Clear Filters
            </button>
          </div>
        </div>
      </div>

      {/* Transactions Table */}
      <div className="bg-white rounded-lg shadow overflow-hidden">
        {loading ? (
          <div className="p-8 text-center">Loading transactions...</div>
        ) : transactions.length === 0 ? (
          <div className="p-8 text-center text-gray-500">
            No transactions found. Create your first AP transaction to get started.
          </div>
        ) : (
          <table className="min-w-full">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Number
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Date
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Supplier
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Type
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Reference
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Amount
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Outstanding
                </th>
                <th className="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Status
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {transactions.map((transaction) => {
                const outstanding = getOutstandingAmount(transaction)
                const typeInfo = transactionTypes.find(t => t.name === transaction.transaction_type_name)
                const isInvoice = typeInfo?.affects_balance === 'credit'
                
                return (
                  <tr key={transaction.id} className="hover:bg-gray-50">
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">
                      {transaction.transaction_number}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                      {formatDate(transaction.transaction_date)}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                      {transaction.supplier_name}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      <span className="flex items-center">
                        {isInvoice ? (
                          <FileText className="h-4 w-4 mr-1 text-red-500" />
                        ) : (
                          <CreditCard className="h-4 w-4 mr-1 text-green-500" />
                        )}
                        {transaction.transaction_type_name}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {transaction.reference || '-'}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900 text-right">
                      {formatCurrency(parseFloat(transaction.amount))}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900 text-right">
                      {outstanding > 0 ? formatCurrency(outstanding) : '-'}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-center">
                      {transaction.is_posted ? (
                        <span className="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-green-100 text-green-800">
                          Posted
                        </span>
                      ) : (
                        <span className="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-yellow-100 text-yellow-800">
                          Unposted
                        </span>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                      <div className="flex justify-end space-x-2">
                        <Link
                          href={`/ap/transactions/${transaction.id}`}
                          className="text-blue-600 hover:text-blue-900"
                        >
                          View
                        </Link>
                        {!transaction.is_posted && (
                          <button
                            onClick={() => handlePost(transaction.id)}
                            className="text-green-600 hover:text-green-900"
                          >
                            Post
                          </button>
                        )}
                        {transaction.is_posted && outstanding > 0 && (
                          <Link
                            href={`/ap/allocations/new?transaction_id=${transaction.id}`}
                            className="text-purple-600 hover:text-purple-900"
                          >
                            Allocate
                          </Link>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
} 