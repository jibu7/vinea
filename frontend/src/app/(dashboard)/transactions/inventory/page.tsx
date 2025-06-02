'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Package, TrendingUp, TrendingDown, RotateCcw } from "lucide-react";
import Link from "next/link";

export default function InventoryTransactionsPage() {
  const transactionTypes = [
    {
      title: "Receipts",
      description: "Record inventory receipts and purchases",
      icon: TrendingUp,
      href: "/transactions/inventory/receipts",
      color: "text-green-600",
      count: "7 pending"
    },
    {
      title: "Issues/Shipments",
      description: "Process inventory issues and shipments",
      icon: TrendingDown,
      href: "/transactions/inventory/issues",
      color: "text-blue-600",
      count: "12 to ship"
    },
    {
      title: "Adjustments",
      description: "Record inventory adjustments and corrections",
      icon: RotateCcw,
      href: "/transactions/inventory/adjustments",
      color: "text-purple-600",
      count: "3 pending"
    },
    {
      title: "Transfers",
      description: "Transfer inventory between locations",
      icon: Package,
      href: "/transactions/inventory/transfers",
      color: "text-orange-600",
      count: "2 in transit"
    }
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Inventory Transactions</h1>
        <p className="text-muted-foreground">
          Process inventory movements, adjustments, and stock transactions
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
            <CardDescription>Latest inventory transactions</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              <div className="flex justify-between items-center">
                <span className="text-sm">Receipt #RCT-2024-012</span>
                <span className="text-sm text-green-600">+150 units</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm">Issue #ISS-2024-089</span>
                <span className="text-sm text-blue-600">-75 units</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm">Adjustment #ADJ-2024-005</span>
                <span className="text-sm text-purple-600">+5 units</span>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Inventory Alerts</CardTitle>
            <CardDescription>Items requiring attention</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              <div className="flex justify-between items-center">
                <span className="text-sm">Low Stock Items</span>
                <span className="text-sm font-medium text-red-600">8 items</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm">Reorder Points</span>
                <span className="text-sm font-medium text-orange-600">3 items</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm">Negative Stock</span>
                <span className="text-sm font-medium text-red-600">1 item</span>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
