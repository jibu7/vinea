'use client';

import { useState } from 'react';
import { FileText, Download, Calendar, DollarSign, Users } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

const reports = [
  {
    id: 'aging',
    title: 'Accounts Payable Aging',
    description: 'View aged payables by supplier and period',
    icon: Calendar,
    category: 'aging',
  },
  {
    id: 'supplier-statement',
    title: 'Supplier Statement',
    description: 'Generate detailed statements for specific suppliers',
    icon: Users,
    category: 'supplier',
  },
  {
    id: 'expenses',
    title: 'Expense Report',
    description: 'Analyze expenses by period, supplier, or transaction type',
    icon: DollarSign,
    category: 'financial',
  },
  {
    id: 'outstanding-bills',
    title: 'Outstanding Bills',
    description: 'List all unpaid supplier invoices with details',
    icon: FileText,
    category: 'operational',
  },
  {
    id: 'payments',
    title: 'Payments Report',
    description: 'Track payment disbursements and due bills',
    icon: DollarSign,
    category: 'operational',
  },
  {
    id: 'supplier-balance',
    title: 'Supplier Balance Summary',
    description: 'View current balances for all suppliers',
    icon: Users,
    category: 'supplier',
  },
];

export default function APReportsPage() {
  const [selectedReport, setSelectedReport] = useState<string | null>(null);
  const [dateFrom, setDateFrom] = useState(
    new Date(new Date().getFullYear(), new Date().getMonth(), 1)
      .toISOString()
      .split('T')[0]
  );
  const [dateTo, setDateTo] = useState(new Date().toISOString().split('T')[0]);
  const [selectedSupplier, setSelectedSupplier] = useState<string>('all');

  const handleGenerateReport = async (reportId: string) => {
    try {
      const params = new URLSearchParams({
        report_type: reportId,
        date_from: dateFrom,
        date_to: dateTo,
        supplier_id: selectedSupplier,
      });

      const response = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL}/api/ap/reports/generate?${params}`,
        {
          credentials: 'include',
        }
      );

      if (response.ok) {
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = `ap-${reportId}-${new Date().toISOString().split('T')[0]}.pdf`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
      }
    } catch (error) {
      console.error('Error generating report:', error);
    }
  };

  return (
    <div className="container mx-auto py-10">
      <div className="mb-8">
        <h1 className="text-3xl font-bold mb-2">AP Reports</h1>
        <p className="text-gray-600">
          Generate and download accounts payable reports
        </p>
      </div>

      <div className="grid gap-6 md:grid-cols-3 mb-8">
        <div className="space-y-2">
          <Label htmlFor="date-from">From Date</Label>
          <Input
            id="date-from"
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="date-to">To Date</Label>
          <Input
            id="date-to"
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="supplier">Supplier</Label>
          <Select value={selectedSupplier} onValueChange={setSelectedSupplier}>
            <SelectTrigger id="supplier">
              <SelectValue placeholder="Select supplier" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Suppliers</SelectItem>
              {/* Supplier list would be fetched from API */}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
        {reports.map((report) => {
          const Icon = report.icon;
          return (
            <Card
              key={report.id}
              className="cursor-pointer hover:shadow-lg transition-shadow"
              onClick={() => setSelectedReport(report.id)}
            >
              <CardHeader>
                <div className="flex items-center justify-between">
                  <Icon className="h-8 w-8 text-primary" />
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleGenerateReport(report.id);
                    }}
                  >
                    <Download className="h-4 w-4 mr-2" />
                    Generate
                  </Button>
                </div>
                <CardTitle className="mt-4">{report.title}</CardTitle>
                <CardDescription>{report.description}</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="text-sm text-gray-500">
                  Category: {report.category}
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {selectedReport && (
        <div className="mt-8 p-6 border rounded-lg bg-gray-50">
          <h2 className="text-xl font-semibold mb-4">
            Report Preview: {reports.find((r) => r.id === selectedReport)?.title}
          </h2>
          <p className="text-gray-600 mb-4">
            Click the Generate button to download the full report.
          </p>
          <div className="flex gap-4">
            <Button onClick={() => handleGenerateReport(selectedReport)}>
              <Download className="h-4 w-4 mr-2" />
              Download PDF
            </Button>
            <Button
              variant="outline"
              onClick={() => handleGenerateReport(selectedReport)}
            >
              <FileText className="h-4 w-4 mr-2" />
              Download Excel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
} 