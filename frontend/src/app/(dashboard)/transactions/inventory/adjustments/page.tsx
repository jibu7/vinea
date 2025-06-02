'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Plus, Calculator } from 'lucide-react';

export default function InventoryAdjustmentsPage() {
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Inventory Adjustments</h1>
          <p className="text-muted-foreground">
            Process inventory quantity and value adjustments
          </p>
        </div>
        <Button>
          <Plus className="mr-2 h-4 w-4" />
          New Adjustment
        </Button>
      </div>

      <div className="grid gap-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Calculator className="h-5 w-5" />
              Inventory Adjustments
            </CardTitle>
            <CardDescription>
              Adjust inventory quantities and values for stock takes and corrections
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="text-center py-12">
              <Calculator className="mx-auto h-12 w-12 text-muted-foreground" />
              <h3 className="mt-4 text-lg font-medium">No adjustments found</h3>
              <p className="mt-2 text-muted-foreground">
                Get started by creating your first inventory adjustment.
              </p>
              <Button className="mt-4">
                <Plus className="mr-2 h-4 w-4" />
                Create Adjustment
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
