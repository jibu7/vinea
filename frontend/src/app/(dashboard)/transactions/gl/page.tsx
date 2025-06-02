'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export default function TransactionsGLPage() {
  return (
    <div className="container mx-auto p-6">
      <h1 className="text-3xl font-bold mb-6">General Ledger Transactions</h1>
      
      <div className="grid grid-cols-1 gap-6">
        <Card>
          <CardHeader>
            <CardTitle>Manual Journal Entry</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-gray-600">Create and manage manual journal entries</p>
            <div className="mt-4 space-x-4">
              <button className="bg-green-500 text-white px-4 py-2 rounded hover:bg-green-600">
                New Journal Entry
              </button>
              <button className="bg-blue-500 text-white px-4 py-2 rounded hover:bg-blue-600">
                View All Entries
              </button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
