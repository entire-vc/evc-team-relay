<script lang="ts">
  import { AlertDialog as AlertDialogPrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';
  import { buttonVariants } from '../button/variants.js';

  type Props = AlertDialogPrimitive.ActionProps & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<AlertDialogPrimitive.Action class={cn(buttonVariants(), className)} {...restProps}>
  {@render children?.()}
</AlertDialogPrimitive.Action>
