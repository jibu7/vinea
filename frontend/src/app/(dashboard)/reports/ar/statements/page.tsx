'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { FileText, Download } from 'lucide-react';

export default function CustomerStatementsPage() {
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Customer Statements</h1>
          <p className="text-muted-foreground">
            Generate customer statements showing transaction history
          </p>
        </div>
        <Button>
          <Download className="mr-2 h-4 w-4" />
          Export Statements
        </Button>
      </div>

      <div className="grid gap-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileText className="h-5 w-5" />
              Customer Statements
            </CardTitle>
            <CardDescription>
              Generate statements for selected customers
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="text-center py-12">
              <FileText className="mx-auto h-12 w-12 text-muted-foreground" />
              <h3 className="mt-4 text-lg font-medium">No statements generated</h3>
              <p className="mt-2 text-muted-foreground">
                Select customers and generate statements to view transaction history.
              </p>
              <Button className="mt-4">
                <FileText className="mr-2 h-4 w-4" />
                Generate Statements
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
