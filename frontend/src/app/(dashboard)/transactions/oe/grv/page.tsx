'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Plus, TruckIcon } from 'lucide-react';

export default function GRVPage() {
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Goods Received Voucher (GRV) Processing</h1>
          <p className="text-muted-foreground">
            Process goods received from suppliers
          </p>
        </div>
        <Button>
          <Plus className="mr-2 h-4 w-4" />
          New GRV
        </Button>
      </div>

      <div className="grid gap-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <TruckIcon className="h-5 w-5" />
              Goods Received Vouchers
            </CardTitle>
            <CardDescription>
              Process and record goods received from suppliers
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="text-center py-12">
              <TruckIcon className="mx-auto h-12 w-12 text-muted-foreground" />
              <h3 className="mt-4 text-lg font-medium">No GRVs found</h3>
              <p className="mt-2 text-muted-foreground">
                Get started by creating your first Goods Received Voucher.
              </p>
              <Button className="mt-4">
                <Plus className="mr-2 h-4 w-4" />
                Create GRV
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
