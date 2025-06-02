'use client';

import { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { 
  Plus, 
  Settings, 
  Edit, 
  Trash2, 
  BookOpen,
  FileText,
  Calculator,
  Eye
} from 'lucide-react';

// Mock data for GL Transaction Types
const mockTransactionTypes = [
  {
    id: 1,
    code: 'JE',
    name: 'Journal Entry',
    description: 'Standard journal entry for manual GL postings',
    isActive: true,
    numberingPrefix: 'JE',
    lastNumber: 1205,
    defaultDebitAccount: '1100 - Cash',
    defaultCreditAccount: '4000 - Sales'
  },
  {
    id: 2,
    code: 'ADJ',
    name: 'Adjustment Entry',
    description: 'Period-end adjustments and corrections',
    isActive: true,
    numberingPrefix: 'ADJ',
    lastNumber: 45,
    defaultDebitAccount: '',
    defaultCreditAccount: ''
  },
  {
    id: 3,
    code: 'DEP',
    name: 'Depreciation Entry',
    description: 'Monthly depreciation calculations',
    isActive: true,
    numberingPrefix: 'DEP',
    lastNumber: 156,
    defaultDebitAccount: '6200 - Depreciation Expense',
    defaultCreditAccount: '1200 - Accumulated Depreciation'
  },
  {
    id: 4,
    code: 'CLS',
    name: 'Closing Entry',
    description: 'Year-end closing entries',
    isActive: false,
    numberingPrefix: 'CLS',
    lastNumber: 12,
    defaultDebitAccount: '',
    defaultCreditAccount: ''
  }
];

export default function GLTransactionTypesPage() {
  const [transactionTypes, setTransactionTypes] = useState(mockTransactionTypes);
  const [isAddingNew, setIsAddingNew] = useState(false);

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">GL Transaction Types</h1>
          <p className="text-muted-foreground">
            Configure transaction types for General Ledger journal entries
          </p>
        </div>
        <Button onClick={() => setIsAddingNew(true)}>
          <Plus className="mr-2 h-4 w-4" />
          New Transaction Type
        </Button>
      </div>

      {/* Summary Cards */}
      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Types</CardTitle>
            <Settings className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{transactionTypes.length}</div>
            <p className="text-xs text-muted-foreground">
              {transactionTypes.filter(t => t.isActive).length} active
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Active Types</CardTitle>
            <BookOpen className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {transactionTypes.filter(t => t.isActive).length}
            </div>
            <p className="text-xs text-muted-foreground">
              Ready for use
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Last Entry</CardTitle>
            <FileText className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">JE-1205</div>
            <p className="text-xs text-muted-foreground">
              Most recent number
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">With Defaults</CardTitle>
            <Calculator className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {transactionTypes.filter(t => t.defaultDebitAccount && t.defaultCreditAccount).length}
            </div>
            <p className="text-xs text-muted-foreground">
              Have default accounts
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Transaction Types Table */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Settings className="h-5 w-5" />
            Transaction Type Configuration
          </CardTitle>
          <CardDescription>
            Manage GL transaction types, numbering sequences, and default accounts
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b">
                  <th className="text-left p-3 font-medium">Code</th>
                  <th className="text-left p-3 font-medium">Name</th>
                  <th className="text-left p-3 font-medium">Description</th>
                  <th className="text-left p-3 font-medium">Numbering</th>
                  <th className="text-left p-3 font-medium">Status</th>
                  <th className="text-left p-3 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {transactionTypes.map((type) => (
                  <tr key={type.id} className="border-b hover:bg-gray-50">
                    <td className="p-3">
                      <span className="font-mono text-sm bg-gray-100 px-2 py-1 rounded">
                        {type.code}
                      </span>
                    </td>
                    <td className="p-3 font-medium">{type.name}</td>
                    <td className="p-3 text-sm text-gray-600">{type.description}</td>
                    <td className="p-3">
                      <div className="text-sm">
                        <div className="font-mono">{type.numberingPrefix}-{type.lastNumber}</div>
                        <div className="text-gray-500">Next: {type.numberingPrefix}-{type.lastNumber + 1}</div>
                      </div>
                    </td>
                    <td className="p-3">
                      <span className={`inline-flex px-2 py-1 text-xs rounded-full ${
                        type.isActive 
                          ? 'bg-green-100 text-green-800' 
                          : 'bg-gray-100 text-gray-600'
                      }`}>
                        {type.isActive ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td className="p-3">
                      <div className="flex gap-2">
                        <Button variant="outline" size="sm">
                          <Eye className="h-4 w-4" />
                        </Button>
                        <Button variant="outline" size="sm">
                          <Edit className="h-4 w-4" />
                        </Button>
                        <Button variant="outline" size="sm">
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      {/* Configuration Guide */}
      <Card>
        <CardHeader>
          <CardTitle>Transaction Type Setup Guide</CardTitle>
          <CardDescription>
            Best practices for configuring GL transaction types
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-6 md:grid-cols-2">
            <div>
              <h3 className="font-semibold mb-3">Required Fields</h3>
              <ul className="space-y-2 text-sm">
                <li className="flex items-start gap-2">
                  <div className="w-2 h-2 bg-blue-500 rounded-full mt-2"></div>
                  <span><strong>Code:</strong> Unique identifier (2-4 characters)</span>
                </li>
                <li className="flex items-start gap-2">
                  <div className="w-2 h-2 bg-blue-500 rounded-full mt-2"></div>
                  <span><strong>Name:</strong> Descriptive name for the transaction type</span>
                </li>
                <li className="flex items-start gap-2">
                  <div className="w-2 h-2 bg-blue-500 rounded-full mt-2"></div>
                  <span><strong>Numbering Prefix:</strong> Document numbering pattern</span>
                </li>
              </ul>
            </div>
            
            <div>
              <h3 className="font-semibold mb-3">Optional Settings</h3>
              <ul className="space-y-2 text-sm">
                <li className="flex items-start gap-2">
                  <div className="w-2 h-2 bg-green-500 rounded-full mt-2"></div>
                  <span><strong>Default Accounts:</strong> Pre-populate journal entries</span>
                </li>
                <li className="flex items-start gap-2">
                  <div className="w-2 h-2 bg-green-500 rounded-full mt-2"></div>
                  <span><strong>Auto-reverse:</strong> Automatic reversing entries</span>
                </li>
                <li className="flex items-start gap-2">
                  <div className="w-2 h-2 bg-green-500 rounded-full mt-2"></div>
                  <span><strong>Approval Required:</strong> Workflow controls</span>
                </li>
              </ul>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
