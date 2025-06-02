'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ShoppingCart, CreditCard, FileText, CheckCircle } from "lucide-react";
import Link from "next/link";

export default function APTransactionsPage() {
  const transactionTypes = [
    {
      title: "Purchase Orders",
      description: "Create and manage purchase orders",
      icon: ShoppingCart,
      href: "/transactions/ap/orders",
      color: "text-blue-600",
      count: "5 pending"
    },
    {
      title: "Vendor Invoices",
      description: "Enter and process vendor invoices",
      icon: FileText,
      href: "/transactions/ap/invoices",
      color: "text-green-600",
      count: "15 to process"
    },
    {
      title: "Vendor Payments",
      description: "Process payments to vendors",
      icon: CreditCard,
      href: "/transactions/ap/payments",
      color: "text-purple-600",
      count: "8 due today"
    },
    {
      title: "Invoice Approval",
      description: "Review and approve pending invoices",
      icon: CheckCircle,
      href: "/transactions/ap/approvals",
      color: "text-orange-600",
      count: "4 awaiting"
    }
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Accounts Payable Transactions</h1>
        <p className="text-muted-foreground">
          Process purchase orders, vendor invoices, and payment transactions
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
            <CardDescription>Latest AP transactions and updates</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              <div className="flex justify-between items-center">
                <span className="text-sm">PO #PO-2024-045</span>
                <span className="text-sm text-blue-600">$3,250.00</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm">Invoice from Tech Supply Co</span>
                <span className="text-sm text-green-600">$1,800.00</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm">Payment to Office Depot</span>
                <span className="text-sm text-purple-600">$475.00</span>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>AP Summary</CardTitle>
            <CardDescription>Current accounts payable overview</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              <div className="flex justify-between items-center">
                <span className="text-sm">Total Outstanding</span>
                <span className="text-sm font-medium">$32,450.00</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm">Due This Week</span>
                <span className="text-sm text-orange-600">$8,750.00</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm">Overdue</span>
                <span className="text-sm text-red-600">$2,100.00</span>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
