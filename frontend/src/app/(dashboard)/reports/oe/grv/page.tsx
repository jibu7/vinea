'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { TruckIcon, Download } from 'lucide-react';

export default function GRVReportsPage() {
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">GRV Listing</h1>
          <p className="text-muted-foreground">
            View detailed listing of all Goods Received Vouchers
          </p>
        </div>
        <Button>
          <Download className="mr-2 h-4 w-4" />
          Export Report
        </Button>
      </div>

      <div className="grid gap-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <TruckIcon className="h-5 w-5" />
              GRV Listing
            </CardTitle>
            <CardDescription>
              Complete listing of all Goods Received Vouchers with details
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="text-center py-12">
              <TruckIcon className="mx-auto h-12 w-12 text-muted-foreground" />
              <h3 className="mt-4 text-lg font-medium">No GRVs found</h3>
              <p className="mt-2 text-muted-foreground">
                Generate your GRV listing to view all goods received details.
              </p>
              <Button className="mt-4">
                <TruckIcon className="mr-2 h-4 w-4" />
                Generate Report
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
