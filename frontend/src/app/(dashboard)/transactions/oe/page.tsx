'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ShoppingCart, Package, Truck, FileText } from "lucide-react";
import Link from "next/link";

export default function OETransactionsPage() {
  const transactionTypes = [
    {
      title: "Sales Orders",
      description: "Create and manage customer sales orders",
      icon: ShoppingCart,
      href: "/transactions/oe/orders",
      color: "text-blue-600",
      count: "18 open"
    },
    {
      title: "Order Picking",
      description: "Pick and prepare orders for shipment",
      icon: Package,
      href: "/transactions/oe/picking",
      color: "text-green-600",
      count: "9 ready"
    },
    {
      title: "Shipments",
      description: "Process and track order shipments",
      icon: Truck,
      href: "/transactions/oe/shipments",
      color: "text-purple-600",
      count: "6 to ship"
    },
    {
      title: "Invoicing",
      description: "Generate invoices from shipped orders",
      icon: FileText,
      href: "/transactions/oe/invoicing",
      color: "text-orange-600",
      count: "11 to invoice"
    }
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Order Entry Transactions</h1>
        <p className="text-muted-foreground">
          Process sales orders from entry through shipment and invoicing
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
            <CardDescription>Latest order processing activity</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              <div className="flex justify-between items-center">
                <span className="text-sm">Order #SO-2024-156</span>
                <span className="text-sm text-green-600">Shipped</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm">Order #SO-2024-157</span>
                <span className="text-sm text-blue-600">Picking</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm">Order #SO-2024-158</span>
                <span className="text-sm text-orange-600">New</span>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Order Pipeline</CardTitle>
            <CardDescription>Orders by status</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              <div className="flex justify-between items-center">
                <span className="text-sm">New Orders</span>
                <span className="text-sm font-medium">18</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm">In Production</span>
                <span className="text-sm font-medium text-blue-600">12</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm">Ready to Ship</span>
                <span className="text-sm font-medium text-green-600">6</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm">Shipped</span>
                <span className="text-sm font-medium text-gray-600">24</span>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
