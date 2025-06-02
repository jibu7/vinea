'use client';

import { ProtectedRoute } from '@/components/layout/protected-route';
import { useAuth } from '@/contexts/AuthContext';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState } from 'react';
import { 
  Home, 
  Users, 
  Building2, 
  BookOpen, 
  DollarSign, 
  ShoppingCart, 
  Package,
  Settings,
  LogOut,
  Shield,
  Calendar,
  ChevronDown,
  ChevronRight,
  FileText,
  CreditCard,
  BarChart3,
  Wrench,
  ArrowUpDown,
  PieChart,
  FolderOpen,
  UserCog,
  Coins,
  Receipt,
  ClipboardList,
  TruckIcon,
  Calculator
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { hasPermission, hasAnyPermission, PERMISSIONS } from '@/lib/permissions';

interface NavItem {
  name: string;
  href: string;
  icon: React.ElementType;
  permission?: string;
  subItems?: NavItem[];
}

interface NavSection {
  name: string;
  icon: React.ElementType;
  permission?: string;
  items: NavItem[];
}

const getNavigationStructure = (): NavSection[] => [
  {
    name: 'Maintenance',
    icon: Wrench,
    items: [
      {
        name: 'System & Company',
        href: '/maintenance/system',
        icon: Settings,
        subItems: [
          { name: 'Company Setup', href: '/maintenance/system/companies', icon: Building2, permission: PERMISSIONS.SYSTEM.COMPANY_SETUP },
          { name: 'User Management', href: '/maintenance/system/users', icon: Users, permission: PERMISSIONS.SYSTEM.USER_MANAGEMENT },
          { name: 'Roles & Permissions', href: '/maintenance/system/roles', icon: Shield, permission: PERMISSIONS.SYSTEM.ROLE_MANAGEMENT },
          { name: 'Accounting Periods', href: '/maintenance/system/accounting-periods', icon: Calendar, permission: PERMISSIONS.SYSTEM.ACCOUNTING_PERIODS },
          { name: 'System Configuration', href: '/maintenance/system/config', icon: UserCog, permission: PERMISSIONS.SYSTEM.SYSTEM_CONFIG },
        ],
      },
      {
        name: 'General Ledger Setup',
        href: '/maintenance/gl',
        icon: BookOpen,
        subItems: [
          { name: 'Chart of Accounts', href: '/maintenance/gl/accounts', icon: FolderOpen, permission: PERMISSIONS.GL.SETUP.CHART_OF_ACCOUNTS },
          { name: 'GL Transaction Types', href: '/maintenance/gl/transaction-types', icon: Settings, permission: PERMISSIONS.GL.SETUP.TRANSACTION_TYPES },
        ],
      },
      {
        name: 'Accounts Receivable Setup',
        href: '/maintenance/ar',
        icon: DollarSign,
        subItems: [
          { name: 'Customer Master', href: '/maintenance/ar/customers', icon: Users, permission: PERMISSIONS.AR.SETUP.CUSTOMERS },
          { name: 'AR Transaction Types', href: '/maintenance/ar/transaction-types', icon: Settings, permission: PERMISSIONS.AR.SETUP.TRANSACTION_TYPES },
        ],
      },
      {
        name: 'Accounts Payable Setup',
        href: '/maintenance/ap',
        icon: CreditCard,
        subItems: [
          { name: 'Supplier Master', href: '/maintenance/ap/suppliers', icon: TruckIcon, permission: PERMISSIONS.AP.SETUP.SUPPLIERS },
          { name: 'AP Transaction Types', href: '/maintenance/ap/transaction-types', icon: Settings, permission: PERMISSIONS.AP.SETUP.TRANSACTION_TYPES },
        ],
      },
      {
        name: 'Inventory Setup',
        href: '/maintenance/inventory',
        icon: Package,
        subItems: [
          { name: 'Inventory Item Master', href: '/maintenance/inventory/items', icon: Package, permission: PERMISSIONS.INVENTORY.SETUP.ITEMS },
          { name: 'Inventory Transaction Types', href: '/maintenance/inventory/transaction-types', icon: Settings, permission: PERMISSIONS.INVENTORY.SETUP.TRANSACTION_TYPES },
        ],
      },
      {
        name: 'Order Entry Setup',
        href: '/maintenance/oe',
        icon: ShoppingCart,
        subItems: [
          { name: 'OE Document Types', href: '/maintenance/oe/document-types', icon: FileText, permission: PERMISSIONS.OE.SETUP.DOCUMENT_TYPES },
        ],
      },
    ],
  },
  {
    name: 'Transactions',
    icon: ArrowUpDown,
    items: [
      {
        name: 'General Ledger',
        href: '/transactions/gl',
        icon: BookOpen,
        subItems: [
          { name: 'Manual Journal Entry', href: '/transactions/gl/journal-entries', icon: FileText, permission: PERMISSIONS.GL.TRANSACTIONS.JOURNAL_ENTRIES },
        ],
      },
      {
        name: 'Accounts Receivable',
        href: '/transactions/ar',
        icon: DollarSign,
        subItems: [
          { name: 'Customer Invoice Processing', href: '/transactions/ar/invoices', icon: Receipt, permission: PERMISSIONS.AR.TRANSACTIONS.INVOICES },
          { name: 'Customer Credit Note Processing', href: '/transactions/ar/credit-notes', icon: FileText, permission: PERMISSIONS.AR.TRANSACTIONS.CREDIT_NOTES },
          { name: 'Customer Payment Processing & Allocation', href: '/transactions/ar/payments', icon: Coins, permission: PERMISSIONS.AR.TRANSACTIONS.PAYMENTS },
        ],
      },
      {
        name: 'Accounts Payable',
        href: '/transactions/ap',
        icon: CreditCard,
        subItems: [
          { name: 'Supplier Invoice Processing', href: '/transactions/ap/invoices', icon: Receipt, permission: PERMISSIONS.AP.TRANSACTIONS.INVOICES },
          { name: 'Supplier Credit Note Processing', href: '/transactions/ap/credit-notes', icon: FileText, permission: PERMISSIONS.AP.TRANSACTIONS.CREDIT_NOTES },
          { name: 'Supplier Payment Processing & Allocation', href: '/transactions/ap/payments', icon: Coins, permission: PERMISSIONS.AP.TRANSACTIONS.PAYMENTS },
        ],
      },
      {
        name: 'Inventory',
        href: '/transactions/inventory',
        icon: Package,
        subItems: [
          { name: 'Inventory Adjustments', href: '/transactions/inventory/adjustments', icon: Calculator, permission: PERMISSIONS.INVENTORY.TRANSACTIONS.ADJUSTMENTS },
        ],
      },
      {
        name: 'Order Entry',
        href: '/transactions/oe',
        icon: ShoppingCart,
        subItems: [
          { name: 'Sales Order Creation/Management', href: '/transactions/oe/sales-orders', icon: ClipboardList, permission: PERMISSIONS.OE.TRANSACTIONS.SALES_ORDERS },
          { name: 'Purchase Order Creation/Management', href: '/transactions/oe/purchase-orders', icon: ShoppingCart, permission: PERMISSIONS.OE.TRANSACTIONS.PURCHASE_ORDERS },
          { name: 'Goods Received Voucher (GRV) Processing', href: '/transactions/oe/grv', icon: TruckIcon, permission: PERMISSIONS.OE.TRANSACTIONS.GRV },
        ],
      },
    ],
  },
  {
    name: 'Reports',
    icon: PieChart,
    items: [
      {
        name: 'Financial Statements & GL Reports',
        href: '/reports/gl',
        icon: BookOpen,
        subItems: [
          { name: 'Trial Balance', href: '/reports/gl/trial-balance', icon: BarChart3, permission: PERMISSIONS.GL.REPORTS.TRIAL_BALANCE },
          { name: 'GL Detail Report', href: '/reports/gl/detail', icon: FileText, permission: PERMISSIONS.GL.REPORTS.GL_DETAIL },
        ],
      },
      {
        name: 'Accounts Receivable Reports',
        href: '/reports/ar',
        icon: DollarSign,
        subItems: [
          { name: 'Customer Ageing Report', href: '/reports/ar/ageing', icon: BarChart3, permission: PERMISSIONS.AR.REPORTS.AGEING },
          { name: 'Customer Statements', href: '/reports/ar/statements', icon: FileText, permission: PERMISSIONS.AR.REPORTS.STATEMENTS },
          { name: 'Customer Listing', href: '/reports/ar/listing', icon: Users, permission: PERMISSIONS.AR.REPORTS.LISTING },
          { name: 'AR Transaction Listing', href: '/reports/ar/transactions', icon: ClipboardList, permission: PERMISSIONS.AR.REPORTS.LISTING },
        ],
      },
      {
        name: 'Accounts Payable Reports',
        href: '/reports/ap',
        icon: CreditCard,
        subItems: [
          { name: 'Supplier Ageing Report', href: '/reports/ap/ageing', icon: BarChart3, permission: PERMISSIONS.AP.REPORTS.AGEING },
          { name: 'Supplier Statements', href: '/reports/ap/statements', icon: FileText, permission: PERMISSIONS.AP.REPORTS.STATEMENTS },
          { name: 'Supplier Listing', href: '/reports/ap/listing', icon: TruckIcon, permission: PERMISSIONS.AP.REPORTS.LISTING },
          { name: 'AP Transaction Listing', href: '/reports/ap/transactions', icon: ClipboardList, permission: PERMISSIONS.AP.REPORTS.LISTING },
        ],
      },
      {
        name: 'Inventory Reports',
        href: '/reports/inventory',
        icon: Package,
        subItems: [
          { name: 'Inventory Item Listing', href: '/reports/inventory/listing', icon: ClipboardList, permission: PERMISSIONS.INVENTORY.REPORTS.LISTING },
          { name: 'Stock Quantity Report', href: '/reports/inventory/quantity', icon: BarChart3, permission: PERMISSIONS.INVENTORY.REPORTS.QUANTITY },
          { name: 'Stock Valuation Report', href: '/reports/inventory/valuation', icon: Calculator, permission: PERMISSIONS.INVENTORY.REPORTS.VALUATION },
        ],
      },
      {
        name: 'Order Entry Reports',
        href: '/reports/oe',
        icon: ShoppingCart,
        subItems: [
          { name: 'Sales Order Listing', href: '/reports/oe/sales-orders', icon: ClipboardList, permission: PERMISSIONS.OE.REPORTS.SALES_ORDERS },
          { name: 'Purchase Order Listing', href: '/reports/oe/purchase-orders', icon: ShoppingCart, permission: PERMISSIONS.OE.REPORTS.PURCHASE_ORDERS },
          { name: 'GRV Listing', href: '/reports/oe/grv', icon: TruckIcon, permission: PERMISSIONS.OE.REPORTS.GRV },
        ],
      },
    ],
  },
];

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const [openSection, setOpenSection] = useState<string | null>(null);
  const [openSubMenu, setOpenSubMenu] = useState<string | null>(null);

  const navigationStructure = getNavigationStructure();

  const toggleSection = (sectionName: string) => {
    setOpenSection(openSection === sectionName ? null : sectionName);
    setOpenSubMenu(null); // Close any open submenu when switching sections
  };

  const toggleSubMenu = (itemName: string) => {
    setOpenSubMenu(openSubMenu === itemName ? null : itemName);
  };

  // Filter navigation items based on user permissions
  const filterNavigationItems = (items: NavItem[]): NavItem[] => {
    return items.filter(item => {
      // If item has a permission requirement, check it
      if (item.permission && !hasPermission(user, item.permission)) {
        return false;
      }

      // If item has sub-items, filter them too
      if (item.subItems) {
        const filteredSubItems = item.subItems.filter(subItem => 
          !subItem.permission || hasPermission(user, subItem.permission)
        );
        // Only show parent item if it has visible sub-items
        return filteredSubItems.length > 0;
      }

      return true;
    }).map(item => ({
      ...item,
      subItems: item.subItems ? item.subItems.filter(subItem => 
        !subItem.permission || hasPermission(user, subItem.permission)
      ) : undefined
    }));
  };

  // Filter sections based on permissions
  const filteredSections = navigationStructure.map(section => ({
    ...section,
    items: filterNavigationItems(section.items)
  })).filter(section => section.items.length > 0);

  return (
    <ProtectedRoute>
      <div className="flex h-screen bg-gray-100">
        {/* Sidebar */}
        <div className="w-64 bg-white shadow-md flex flex-col">
          <div className="p-4 border-b">
            <h1 className="text-2xl font-bold text-gray-800">vinea</h1>
            <p className="text-sm text-gray-600">Core ERP System</p>
          </div>
          
          <nav className="mt-4 flex-grow overflow-y-auto">
            {/* Dashboard Link */}
            <Link
              href="/dashboard"
              className={cn(
                "flex items-center px-4 py-2 text-sm font-medium transition-colors mb-2",
                pathname === '/dashboard'
                  ? "bg-gray-100 text-gray-900 border-l-4 border-primary"
                  : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
              )}
            >
              <Home className="mr-3 h-5 w-5" />
              Dashboard
            </Link>

            {/* Three-tier Navigation Structure */}
            {filteredSections.map((section) => {
              const SectionIcon = section.icon;
              const isSectionOpen = openSection === section.name;
              const hasSectionAccess = !section.permission || hasPermission(user, section.permission);

              if (!hasSectionAccess) return null;

              return (
                <div key={section.name} className="mb-2">
                  <button
                    onClick={() => toggleSection(section.name)}
                    className={cn(
                      "flex items-center justify-between w-full px-4 py-2 text-sm font-semibold transition-colors",
                      isSectionOpen
                        ? "bg-gray-50 text-gray-900"
                        : "text-gray-700 hover:bg-gray-50 hover:text-gray-900"
                    )}
                  >
                    <div className="flex items-center">
                      <SectionIcon className="mr-3 h-5 w-5" />
                      {section.name}
                    </div>
                    {isSectionOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                  </button>

                  {isSectionOpen && (
                    <div className="pl-2 border-l border-gray-200 ml-2">
                      {section.items.map((item) => {
                        const Icon = item.icon;
                        const isActive = pathname === item.href || (item.subItems && item.subItems.some(sub => pathname.startsWith(sub.href)));
                        const isSubMenuOpen = openSubMenu === item.name && item.subItems;

                        if (item.subItems) {
                          return (
                            <div key={item.name}>
                              <button
                                onClick={() => toggleSubMenu(item.name)}
                                className={cn(
                                  "flex items-center justify-between w-full px-4 py-2 text-sm font-medium transition-colors",
                                  isActive
                                    ? "bg-gray-100 text-gray-900 border-l-4 border-primary"
                                    : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
                                )}
                              >
                                <div className="flex items-center">
                                  <Icon className="mr-3 h-4 w-4" />
                                  {item.name}
                                </div>
                                {isSubMenuOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                              </button>
                              {isSubMenuOpen && (
                                <div className="pl-4 border-l border-gray-200 ml-2">
                                  {item.subItems.map((subItem) => {
                                    const SubIcon = subItem.icon;
                                    const isSubActive = pathname === subItem.href;
                                    return (
                                      <Link
                                        key={subItem.name}
                                        href={subItem.href}
                                        className={cn(
                                          "flex items-center px-4 py-2 text-sm font-medium transition-colors mt-1",
                                          isSubActive
                                            ? "text-primary font-semibold"
                                            : "text-gray-500 hover:text-gray-900"
                                        )}
                                      >
                                        <SubIcon className="mr-3 h-3 w-3" />
                                        {subItem.name}
                                      </Link>
                                    );
                                  })}
                                </div>
                              )}
                            </div>
                          );
                        }

                        return (
                          <Link
                            key={item.name}
                            href={item.href}
                            className={cn(
                              "flex items-center px-4 py-2 text-sm font-medium transition-colors",
                              isActive
                                ? "bg-gray-100 text-gray-900 border-l-4 border-primary"
                                : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
                            )}
                          >
                            <Icon className="mr-3 h-4 w-4" />
                            {item.name}
                          </Link>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </nav>
          
          <div className="p-4 border-t mt-auto">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-900">
                  {user?.first_name} {user?.last_name}
                </p>
                <p className="text-xs text-gray-500">{user?.username}</p>
              </div>
              <button
                onClick={logout}
                className="p-2 text-gray-400 hover:text-gray-600"
                title="Logout"
              >
                <LogOut className="h-5 w-5" />
              </button>
            </div>
          </div>
        </div>
        
        {/* Main content */}
        <div className="flex-1 overflow-y-auto p-6">
          {children}
        </div>
      </div>
    </ProtectedRoute>
  );
}