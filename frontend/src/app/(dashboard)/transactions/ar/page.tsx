'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Receipt, CreditCard, DollarSign, FileText } from "lucide-react";
import Link from "next/link";

export default function ARTransactionsPage() {
  const transactionTypes = [
    {
      title: "Sales Invoices",
      description: "Create and manage customer invoices",
      icon: Receipt,
      href: "/transactions/ar/invoices",
      color: "text-blue-600",
      count: "12 pending"
    },
    {
      title: "Customer Payments",
      description: "Record and apply customer payments",
      icon: CreditCard,
      href: "/transactions/ar/payments",
      color: "text-green-600",
      count: "8 unapplied"
    },
    {
      title: "Credit Memos",
      description: "Issue credit memos and adjustments",
      icon: DollarSign,
      href: "/transactions/ar/credits",
      color: "text-purple-600",
      count: "3 pending"
    },
    {
      title: "Customer Statements",
      description: "Generate and send customer statements",
      icon: FileText,
      href: "/transactions/ar/statements",
      color: "text-orange-600",
      count: "Monthly due"
    }
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Accounts Receivable Transactions</h1>
        <p className="text-muted-foreground">
          Process customer invoices, payments, and related AR transactions
        </p>
      </div>

      <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-2">
        {transactionTypes.map((item) => {
          const IconComponent = item.icon;
          return (
            <Card key={item.href} className="hover:shadow-md transition-shadow">
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <div className="flex items-center space-x-4">
                  <div className={`p-2 rounded-lg bg-gray-100 ${item.color}`}>
                    <IconComponent className="h-6 w-6" />
                  </div>
                  <div>
                    <CardTitle className="text-base">{item.title}</CardTitle>
                    <CardDescription className="text-sm">
                      {item.description}
                    </CardDescription>
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-sm font-medium text-muted-foreground">
                    {item.count}
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                <Link href={item.href}>
                  <Button variant="outline" className="w-full">
                    Open
                  </Button>
                </Link>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Recent Activity</CardTitle>
            <CardDescription>Latest AR transactions and updates</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              <div className="flex justify-between items-center">
                <span className="text-sm">Invoice #INV-2024-001</span>
                <span className="text-sm text-green-600">$1,250.00</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm">Payment from ACME Corp</span>
                <span className="text-sm text-blue-600">$2,500.00</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm">Credit Memo #CM-2024-003</span>
                <span className="text-sm text-purple-600">-$150.00</span>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>AR Summary</CardTitle>
            <CardDescription>Current accounts receivable overview</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              <div className="flex justify-between items-center">
                <span className="text-sm">Total Outstanding</span>
                <span className="text-sm font-medium">$25,750.00</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm">Current (0-30 days)</span>
                <span className="text-sm text-green-600">$18,500.00</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm">Past Due (31+ days)</span>
                <span className="text-sm text-red-600">$7,250.00</span>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
