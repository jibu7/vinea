'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowLeft, FileText, CreditCard } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { api } from '@/lib/api';
import { formatCurrency, formatDate } from '@/lib/utils';

interface APTransaction {
  id: number;
  transaction_number: string;
  transaction_date: string;
  due_date?: string;
  supplier_name?: string;
  transaction_type_name?: string;
  reference?: string;
  description?: string;
  amount: string;
  allocated_amount: string;
  is_posted: boolean;
  is_allocated: boolean;
}

export default function APTransactionDetailPage({ params }: { params: { id: string } }) {
  const router = useRouter();
  const [transaction, setTransaction] = useState<APTransaction | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchTransaction();
  }, [params.id]);

  const fetchTransaction = async () => {
    try {
      const response = await api.get(`/ap/transactions/${params.id}`);
      setTransaction(response.data);
    } catch (error) {
      console.error('Error fetching transaction:', error);
    } finally {
      setLoading(false);
    }
  };

  const handlePost = async () => {
    if (!confirm('Are you sure you want to post this transaction?')) return;
    
    try {
      await api.post(`/ap/transactions/${params.id}/post`);
      await fetchTransaction();
    } catch (error: any) {
      console.error('Failed to post transaction:', error);
      alert(error.response?.data?.detail || 'Failed to post transaction');
    }
  };

  if (loading) {
    return <div className="p-8 text-center">Loading transaction...</div>;
  }

  if (!transaction) {
    return <div className="p-8 text-center">Transaction not found</div>;
  }

  const outstanding = parseFloat(transaction.amount) - parseFloat(transaction.allocated_amount);

  return (
    <div className="container mx-auto py-10">
      <div className="flex items-center gap-4 mb-6">
        <Button
          variant="ghost"
          size="icon"
          onClick={() => router.push('/ap')}
        >
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <h1 className="text-3xl font-bold">AP Transaction Details</h1>
      </div>

      <div className="max-w-4xl space-y-6">
        <div className="bg-white rounded-lg shadow p-6">
          <div className="flex justify-between items-start mb-6">
            <div>
              <h2 className="text-2xl font-semibold">{transaction.transaction_number}</h2>
              <p className="text-gray-500">{transaction.transaction_type_name}</p>
            </div>
            <div className="text-right">
              {transaction.is_posted ? (
                <span className="px-3 py-1 text-sm font-semibold rounded-full bg-green-100 text-green-800">
                  Posted
                </span>
              ) : (
                <span className="px-3 py-1 text-sm font-semibold rounded-full bg-yellow-100 text-yellow-800">
                  Unposted
                </span>
              )}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-6">
            <div>
              <h3 className="text-sm font-medium text-gray-500">Supplier</h3>
              <p className="mt-1">{transaction.supplier_name || '-'}</p>
            </div>

            <div>
              <h3 className="text-sm font-medium text-gray-500">Transaction Date</h3>
              <p className="mt-1">{formatDate(transaction.transaction_date)}</p>
            </div>

            <div>
              <h3 className="text-sm font-medium text-gray-500">Due Date</h3>
              <p className="mt-1">{transaction.due_date ? formatDate(transaction.due_date) : '-'}</p>
            </div>

            <div>
              <h3 className="text-sm font-medium text-gray-500">Reference</h3>
              <p className="mt-1">{transaction.reference || '-'}</p>
            </div>

            <div>
              <h3 className="text-sm font-medium text-gray-500">Amount</h3>
              <p className="mt-1 text-lg font-semibold">{formatCurrency(parseFloat(transaction.amount))}</p>
            </div>

            <div>
              <h3 className="text-sm font-medium text-gray-500">Outstanding</h3>
              <p className="mt-1 text-lg font-semibold">{formatCurrency(outstanding)}</p>
            </div>

            {transaction.description && (
              <div className="col-span-2">
                <h3 className="text-sm font-medium text-gray-500">Description</h3>
                <p className="mt-1">{transaction.description}</p>
              </div>
            )}
          </div>

          <div className="mt-6 flex gap-3">
            {!transaction.is_posted && (
              <Button onClick={handlePost} variant="default">
                Post Transaction
              </Button>
            )}
            {transaction.is_posted && outstanding > 0 && (
              <Button
                onClick={() => router.push(`/ap/allocations/new?transaction_id=${transaction.id}`)}
                variant="outline"
              >
                Allocate
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
} 