'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export default function ReportsGLPage() {
  return (
    <div className="container mx-auto p-6">
      <h1 className="text-3xl font-bold mb-6">General Ledger Reports</h1>
      
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card>
          <CardHeader>
            <CardTitle>Trial Balance</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-gray-600">Generate trial balance report for all accounts</p>
            <div className="mt-4">
              <button className="bg-blue-500 text-white px-4 py-2 rounded hover:bg-blue-600">
                Generate Trial Balance
              </button>
            </div>
          </CardContent>
        </Card>
        
        <Card>
          <CardHeader>
            <CardTitle>GL Detail Report</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-gray-600">Detailed transaction report for general ledger accounts</p>
            <div className="mt-4">
              <button className="bg-blue-500 text-white px-4 py-2 rounded hover:bg-blue-600">
                Generate Detail Report
              </button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
