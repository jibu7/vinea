'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export default function MaintenanceGLPage() {
  return (
    <div className="container mx-auto p-6">
      <h1 className="text-3xl font-bold mb-6">General Ledger Setup</h1>
      
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card>
          <CardHeader>
            <CardTitle>Chart of Accounts</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-gray-600">Manage your general ledger accounts structure</p>
            <div className="mt-4">
              <button className="bg-blue-500 text-white px-4 py-2 rounded hover:bg-blue-600">
                Manage Accounts
              </button>
            </div>
          </CardContent>
        </Card>
        
        <Card>
          <CardHeader>
            <CardTitle>GL Transaction Types</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-gray-600">Configure transaction types for journal entries</p>
            <div className="mt-4">
              <button className="bg-blue-500 text-white px-4 py-2 rounded hover:bg-blue-600">
                Manage Transaction Types
              </button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
