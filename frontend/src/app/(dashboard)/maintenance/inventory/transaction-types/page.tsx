'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Plus, Settings } from 'lucide-react';

export default function InventoryTransactionTypesPage() {
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Inventory Transaction Types</h1>
          <p className="text-muted-foreground">
            Configure inventory transaction types for adjustments and movements
          </p>
        </div>
        <Button>
          <Plus className="mr-2 h-4 w-4" />
          New Transaction Type
        </Button>
      </div>

      <div className="grid gap-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Settings className="h-5 w-5" />
              Transaction Type Configuration
            </CardTitle>
            <CardDescription>
              Define transaction types for inventory movements and adjustments
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="text-center py-12">
              <Settings className="mx-auto h-12 w-12 text-muted-foreground" />
              <h3 className="mt-4 text-lg font-medium">No transaction types configured</h3>
              <p className="mt-2 text-muted-foreground">
                Get started by creating your first inventory transaction type.
              </p>
              <Button className="mt-4">
                <Plus className="mr-2 h-4 w-4" />
                Create Transaction Type
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
