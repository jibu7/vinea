'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Calculator, Download } from 'lucide-react';

export default function StockValuationPage() {
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Stock Valuation Report</h1>
          <p className="text-muted-foreground">
            View inventory valuation based on current stock and costs
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
              <Calculator className="h-5 w-5" />
              Stock Valuation Report
            </CardTitle>
            <CardDescription>
              Current inventory value based on quantities and costs
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="text-center py-12">
              <Calculator className="mx-auto h-12 w-12 text-muted-foreground" />
              <h3 className="mt-4 text-lg font-medium">No valuation data available</h3>
              <p className="mt-2 text-muted-foreground">
                Generate your stock valuation report to view inventory values.
              </p>
              <Button className="mt-4">
                <Calculator className="mr-2 h-4 w-4" />
                Generate Report
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
