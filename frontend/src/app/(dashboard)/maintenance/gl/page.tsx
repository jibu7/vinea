'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import Link from 'next/link';
import { 
  FolderOpen, 
  Settings, 
  BookOpen, 
  Plus, 
  BarChart3, 
  ArrowRight,
  FileText,
  Calculator
} from 'lucide-react';

export default function MaintenanceGLPage() {
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">General Ledger Setup</h1>
          <p className="text-muted-foreground">
            Configure your chart of accounts and transaction types for the General Ledger module
          </p>
        </div>
        <div className="flex gap-2">
          <Link href="/maintenance/gl/chart-of-accounts">
            <Button variant="outline">
              <FolderOpen className="mr-2 h-4 w-4" />
              Chart of Accounts
            </Button>
          </Link>
          <Link href="/maintenance/gl/transaction-types">
            <Button>
              <Settings className="mr-2 h-4 w-4" />
              Transaction Types
            </Button>
          </Link>
        </div>
      </div>

      {/* Overview Cards */}
      <div className="grid gap-6 md:grid-cols-2">
        <Card className="hover:shadow-md transition-shadow">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <div className="flex items-center space-x-2">
              <FolderOpen className="h-5 w-5 text-blue-600" />
              <CardTitle className="text-base font-semibold">Chart of Accounts</CardTitle>
            </div>
            <span className="px-2 py-1 bg-blue-100 text-blue-700 text-xs rounded-md font-medium">Core Setup</span>
          </CardHeader>
          <CardContent>
            <CardDescription className="mb-4">
              Set up and manage your general ledger account structure. Define account types, 
              categories, and hierarchies for proper financial reporting.
            </CardDescription>
            
            <div className="space-y-3 mb-4">
              <div className="flex items-center text-sm">
                <Calculator className="h-4 w-4 mr-2 text-green-600" />
                <span>Account Types: Assets, Liabilities, Equity, Income, Expenses</span>
              </div>
              <div className="flex items-center text-sm">
                <BarChart3 className="h-4 w-4 mr-2 text-blue-600" />
                <span>Account Hierarchies & Grouping</span>
              </div>
              <div className="flex items-center text-sm">
                <FileText className="h-4 w-4 mr-2 text-purple-600" />
                <span>Financial Statement Mapping</span>
              </div>
            </div>

            <div className="flex gap-2">
              <Link href="/maintenance/gl/chart-of-accounts" className="flex-1">
                <Button className="w-full" size="sm">
                  <FolderOpen className="mr-2 h-4 w-4" />
                  Manage Accounts
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Button>
              </Link>
            </div>
          </CardContent>
        </Card>

        <Card className="hover:shadow-md transition-shadow">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <div className="flex items-center space-x-2">
              <Settings className="h-5 w-5 text-orange-600" />
              <CardTitle className="text-base font-semibold">GL Transaction Types</CardTitle>
            </div>
            <span className="px-2 py-1 bg-orange-100 text-orange-700 text-xs rounded-md font-medium">Configuration</span>
          </CardHeader>
          <CardContent>
            <CardDescription className="mb-4">
              Configure transaction types for journal entries. Define default accounts, 
              numbering sequences, and posting behavior for different types of GL transactions.
            </CardDescription>
            
            <div className="space-y-3 mb-4">
              <div className="flex items-center text-sm">
                <BookOpen className="h-4 w-4 mr-2 text-indigo-600" />
                <span>Journal Entry Types</span>
              </div>
              <div className="flex items-center text-sm">
                <Settings className="h-4 w-4 mr-2 text-gray-600" />
                <span>Default Account Assignments</span>
              </div>
              <div className="flex items-center text-sm">
                <Plus className="h-4 w-4 mr-2 text-green-600" />
                <span>Numbering Sequences</span>
              </div>
            </div>

            <div className="flex gap-2">
              <Link href="/maintenance/gl/transaction-types" className="flex-1">
                <Button className="w-full" variant="outline" size="sm">
                  <Settings className="mr-2 h-4 w-4" />
                  Configure Types
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Button>
              </Link>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Quick Setup Guide */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <BookOpen className="h-5 w-5" />
            General Ledger Setup Guide
          </CardTitle>
          <CardDescription>
            Follow these steps to properly configure your General Ledger module
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-4">
              <h3 className="font-semibold text-sm uppercase tracking-wide text-muted-foreground">
                Required Setup Steps
              </h3>
              <div className="space-y-3">
                <div className="flex items-start space-x-3">
                  <div className="flex h-6 w-6 items-center justify-center rounded-full bg-blue-100 text-blue-600 text-xs font-semibold">
                    1
                  </div>
                  <div>
                    <p className="font-medium">Set up Chart of Accounts</p>
                    <p className="text-sm text-muted-foreground">
                      Create your account structure with proper account types and hierarchies
                    </p>
                  </div>
                </div>
                <div className="flex items-start space-x-3">
                  <div className="flex h-6 w-6 items-center justify-center rounded-full bg-orange-100 text-orange-600 text-xs font-semibold">
                    2
                  </div>
                  <div>
                    <p className="font-medium">Configure Transaction Types</p>
                    <p className="text-sm text-muted-foreground">
                      Define journal entry types and default posting behavior
                    </p>
                  </div>
                </div>
              </div>
            </div>
            
            <div className="space-y-4">
              <h3 className="font-semibold text-sm uppercase tracking-wide text-muted-foreground">
                Best Practices
              </h3>
              <div className="space-y-2 text-sm text-muted-foreground">
                <p>• Use consistent account numbering conventions</p>
                <p>• Group related accounts for better reporting</p>
                <p>• Set up control accounts for sub-ledgers</p>
                <p>• Define clear transaction type purposes</p>
                <p>• Test posting behavior before going live</p>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
